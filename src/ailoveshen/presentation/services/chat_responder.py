"""ChatResponder: 配信のチャットのコメントに、1 つずつ声で返事をする。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from loguru import logger

from ailoveshen.application.ports.output.chat_sink import IChatSink
from ailoveshen.application.ports.output.chat_source import IChatSource
from ailoveshen.application.use_cases.comment_triage import Triage
from ailoveshen.application.use_cases.readings import COMMAND, VIEWER, NameReadings
from ailoveshen.domain.entities import PlaySession
from ailoveshen.domain.value_objects import ChatComment
from ailoveshen.presentation.services.llm_service import LLMService

# 一覧を出す間（同じ一覧でチャットを埋めない）
HELP_COOLDOWN_SECONDS = 30.0


@dataclass(frozen=True)
class ChatCommand:
    """チャットのコマンド 1 つ: 呼び名（最初が代表）、使い方、説明。"""

    names: tuple[str, ...]
    usage: str
    description: str


YOMI = ChatCommand(
    ("yomi", "読み", "よみ"), "!yomi <よみ>", "名前の読み方を教える（例: !yomi ねこまる）"
)
HELP = ChatCommand(("commands", "help", "コマンド"), "!commands", "この一覧")


class ChatResponder:
    """
    チャットのコメントを読み、返事を作って話す（docs/streaming.md）。

    - 返事は 1 つずつ作る。作っている間に来たコメントは、新しいものから `backlog` 件だけ
      取っておき、古いものは捨てる（多いときに遅れを積み上げない）
    - 返事と返事の間は `min_interval_seconds` 以上空ける
    - `!` で始まるコメント（コマンド）には返事をしない。自分のコマンドは実行する:
      `!yomi よみ`（`!読み` / `!よみ`）は、その人の名前の読みを覚えて短く応える（設計書 30）、
      `!commands`（`!help` / `!コマンド`）は、使えるコマンドの一覧をチャットに書く（書けなければ声で）
    - `quiet` のアカウント（配信者自身）のコメントには返事をしない（コマンドは実行する）。
      `ignore` のアカウント（ボット）には何も反応しない
    - 返事はプレイ中のセッションを見て作る。視聴者の頼みは中目標になることがある
      （`LLMService.generate_response`）
    """

    def __init__(
        self,
        llm: LLMService,
        session: Callable[[], PlaySession | None],
        say: Callable[[str], Awaitable[None]],
        min_interval_seconds: float = 5.0,
        backlog: int = 3,
        clock: Callable[[], float] = time.monotonic,
        readings: NameReadings | None = None,
        chat: IChatSink | None = None,
        quiet: Iterable[str] = (),
        ignore: Iterable[str] = (),
        refresh: Callable[[], Awaitable[None]] | None = None,
        triage: Callable[[ChatComment], Awaitable[Triage]] | None = None,
    ) -> None:
        """
        Args:
            llm: 返事を作る
            session: プレイ中のセッション（始まる前は None）
            say: 返事の出し先（TTS とログ）
            min_interval_seconds: 返事と返事の最短の間
            backlog: 返事を待つコメントを何件まで取っておくか
            clock: 時計（テストで差し替える）
            readings: 視聴者の名前の読みの辞書（None: `!yomi` も読まない）
            chat: チャットへの書き込み（`!commands` の一覧。None か書けなければ声で言う）
            quiet: 返事をしないアカウントのログイン名（配信者自身、書き込むアカウント。コマンドは
                実行する）
            ignore: 何にも反応しないアカウントのログイン名（ボット: StreamElements など）
            refresh: 返事を作る前にゲームの様子を取り直す（行動の途中の変化: 道具が壊れた、など）
            triage: 返事を作る前の仕分け（Jev。docs/design/34 §5）。返事の要らないものは飛ばし、
                取り下げと頼みを先にする。None なら全部に古い順に返事をする
        """
        self._llm = llm
        self._session = session
        self._say = say
        self._interval = min_interval_seconds
        self._queue: deque[ChatComment] = deque(maxlen=max(1, backlog))
        self._arrived = asyncio.Event()
        self._clock = clock
        self._last_reply_at: float | None = None
        self._readings = readings
        self._acks: list[asyncio.Task[object]] = []
        self._chat = chat
        self._quiet = {q.strip().lower() for q in quiet if q.strip()}
        self._ignore = {q.strip().lower() for q in ignore if q.strip()}
        self._help_at: float | None = None
        self._refresh = refresh
        self._triage = triage
        self._triaged: dict[int, Triage] = {}
        self.skipped = 0  # 仕分けで返事をしなかったコメントの数
        self.dropped = 0  # 返事をせずに捨てたコメントの数

    async def run(self, source: IChatSource) -> None:
        """キャンセルされるまで、読んで返事をする。"""
        reader = asyncio.create_task(self._read(source))
        reader.add_done_callback(_report_reader_error)
        try:
            await self._answer_forever()
        finally:
            reader.cancel()

    def accept(self, comment: ChatComment) -> None:
        """コメントを 1 つ受け取る（返事の順番を待つ）。"""
        logger.info(f"[chat] {comment.user_name}: {comment.message}")
        login = (comment.login or "").lower()
        if login in self._ignore:
            return
        if comment.message.lstrip().startswith("!"):
            self._command(comment)
            return
        if login in self._quiet:
            return
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
            logger.info(f"返事が追いつかないので古いコメントを飛ばした（{self.dropped} 件目）")
        self._queue.append(comment)
        self._arrived.set()

    @property
    def commands(self) -> tuple[ChatCommand, ...]:
        """今使えるコマンド（`!commands` の一覧に出るもの）。"""
        return ((YOMI,) if self._readings is not None else ()) + (HELP,)

    def help_text(self) -> str:
        """コマンドの一覧（チャットに書く 1 行。`!` で始めない: 自分のコマンドとして読まない）。"""
        items = " / ".join(f"{c.usage}（{c.description}）" for c in self.commands)
        return f"使えるコマンド: {items}"

    def _command(self, comment: ChatComment) -> None:
        words = comment.message.strip()[1:].split(maxsplit=1)
        if words and words[0].lower() in HELP.names:
            self._help()
            return
        match = COMMAND.match(comment.message)
        if self._readings is None or match is None:
            return
        learned = self._readings.learn(comment.user_name, match.group(1), VIEWER)
        if learned is None:
            return
        # 読み上げで名前が読みに置き換わる。返事の順番は待たない（短い決まった一言）
        self._spawn(self._say(f"{comment.user_name}さん、読み方覚えたよ！"))

    def _help(self) -> None:
        """`!commands`: 一覧をチャットに書く（書けなければ声で）。続けて呼ばれたら飛ばす。"""
        now = self._clock()
        if self._help_at is not None and now - self._help_at < HELP_COOLDOWN_SECONDS:
            return
        self._help_at = now
        self._spawn(self._post_help())

    async def _post_help(self) -> None:
        text = self.help_text()
        if self._chat is not None and self._chat.can_post and await self._chat.post(text):
            return
        logger.info("チャットに書き込めないので、コマンドの一覧を声で言う")
        await self._say(text)

    def _spawn(self, work: Awaitable[object]) -> None:
        task = asyncio.ensure_future(work)
        self._acks = [t for t in self._acks if not t.done()] + [task]

    async def answer_next(self) -> bool:
        """待っているコメントのうち一番古いものに返事をする。なければ偽。"""
        if not self._queue:
            self._arrived.clear()
            return False
        if self._last_reply_at is not None:
            wait = self._last_reply_at + self._interval - self._clock()
            if wait > 0:
                await asyncio.sleep(wait)
        comment = await self._next_comment()
        if comment is None:
            return True
        started = self._clock()
        if self._refresh is not None:
            await self._refresh()
        reply = await self._llm.generate_response(
            comment.user_name, comment.message, user_id=comment.user_id, session=self._session()
        )
        self._last_reply_at = self._clock()
        if not reply:
            logger.warning(f"{comment.user_name} への返事を作れなかった")
            return True
        logger.info(f"[reply] ({self._last_reply_at - started:.1f}s) {reply}")
        await self._say(reply)
        return True

    async def _next_comment(self) -> ChatComment | None:
        """
        次に返事をするコメント（仕分けがあれば、取り下げと頼みを先に）。返事の要らないものだった
        ときは None（そのコメントは捨てる）。
        """
        if self._triage is None:
            return self._queue.popleft()
        # 仕分けを待つ間にもコメントは届く（古いものは押し出される）: 仕分けていないものがなくなるまで
        while True:
            fresh = next((c for c in self._queue if id(c) not in self._triaged), None)
            if fresh is None:
                break
            self._triaged[id(fresh)] = await self._triage(fresh)
        if not self._queue:
            self._triaged.clear()
            return None
        queued = list(self._queue)
        comment = next((c for c in queued if self._triaged[id(c)].urgent), queued[0])
        self._queue.remove(comment)
        result = self._triaged.pop(id(comment))
        self._triaged = {k: v for k, v in self._triaged.items() if k in {id(c) for c in self._queue}}
        if not result.answer:
            self.skipped += 1
            logger.info(
                f"[chat] 返事しない（{result.kind}、確信度 {result.confidence:.2f}）: "
                f"{comment.user_name}: {comment.message}"
            )
            return None
        return comment

    async def _answer_forever(self) -> None:
        while True:
            try:
                answered = await self.answer_next()
            except Exception as e:  # noqa: BLE001 - 1 つの返事の失敗でチャットを止めない
                logger.opt(exception=e).warning(f"返事に失敗した（{type(e).__name__}: {e}）")
                continue
            if not answered:
                await self._arrived.wait()

    async def _read(self, source: IChatSource) -> None:
        async for comment in source.comments():
            self.accept(comment)
        logger.error("チャットの読み込みが終わった（もうコメントは届かない）")


def _report_reader_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        error = task.exception()
        logger.opt(exception=error).error(
            f"チャットの読み込みが止まった（{type(error).__name__}: {error}）"
        )
