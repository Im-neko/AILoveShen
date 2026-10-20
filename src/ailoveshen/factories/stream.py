"""本番の配信のファクトリー（Composition Root、docs/streaming.md）。"""

from __future__ import annotations

import asyncio
import socket
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from ailoveshen.application.use_cases.presets import SpeechPresets
from ailoveshen.application.use_cases.foreign_speech import ForeignSpeech
from ailoveshen.application.use_cases.readings import NameReadings
from ailoveshen.application.use_cases.self_facts import SelfFacts
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import EmotionState, EmotionType, MessageType, SpeechPriority
from ailoveshen.factories.game import create_game_service
from ailoveshen.factories.llm import (
    create_comment_triage,
    create_fact_check,
    create_llm_service,
    create_promise_check,
)
from ailoveshen.factories.text import create_text_generator
from ailoveshen.infrastructure.adapters.storage import (
    InMemoryGenerationLog,
    JsonlGenerationLog,
    JsonPresetStore,
    JsonReadingStore,
    JsonSelfFactStore,
)
from ailoveshen.infrastructure.config import Settings
from ailoveshen.infrastructure.events import AsyncEventBus
from ailoveshen.presentation.services import ChatResponder, Narrator, SupportResponder
from ailoveshen.presentation.services.stream import Stream


# 管理画面の指示の送り主（視聴者ではない。docs/design/54_admin_panel.md）
PRODUCER_NAME = "ディレクター"


class StreamSetupError(RuntimeError):
    """配信を始められない（TTS サーバーにつながらないなど）。理由と直し方を持つ。"""


async def create_stream(settings: Settings, tts_config: dict[str, Any], base_dir: Path) -> Stream:
    """
    配信の全部をつないだ Stream を作る。

    - 読み上げ（`stream.speak`）: TTS サーバーにつながらなければ始めない（黙った配信を防ぐ）
    - 目標ボード・アバター（`stream.board_port`、0 なら出さない）
    - Twitch のチャット（`twitch.enabled` かつ `twitch.channel`）: 匿名で読むだけ

    Args:
        settings: 設定
        tts_config: 設定の tts セクション（辞書。TTS のファクトリーが辞書で受け取る）
        base_dir: リポジトリの直下（アバターのモデルと記録の基準）

    Raises:
        StreamSetupError: 目標ボードのポートが使われている、または読み上げがオンで TTS サーバーに
            つながらないとき
    """
    if settings.stream.board_port:
        _check_port_free(settings.stream.board_port)
    bus = AsyncEventBus()
    conversation = Conversation()
    gemini_calls = (
        JsonlGenerationLog(
            str(base_dir / settings.llm.generation_log_dir),
            settings.llm.debug_log_size,
            settings.llm.generation_log_keep_days,
        )
        if settings.llm.generation_log_dir
        else InMemoryGenerationLog(settings.llm.debug_log_size)
    )
    # 視聴者の名前の読み（docs/design/30_name_readings.md）: 返答で覚え、読み上げで使う
    readings = NameReadings(JsonReadingStore(base_dir / settings.twitch.readings_path))
    # 自分について話したこと（docs/design/40_small_talk.md）: 返答で覚え、次の返答で守る
    self_facts = SelfFacts(JsonSelfFactStore(base_dir / settings.twitch.self_facts_path))
    # ゲームと返事で 1 つの生成器を共有する（同時に送る数をまとめて数える）
    shared_text = create_text_generator(settings.llm, gemini_calls)
    game = create_game_service(
        jev=settings.jev,
        minecraft=settings.minecraft,
        character=settings.character,
        event_publisher=bus,
        conversation=conversation,
        generation_log=gemini_calls,
        llm=settings.llm,
        text_generator=shared_text,
        obs=settings.obs,
    )
    # 返事が約束だけで何もしないなら書き直させる（設計書 34 §10）。返事はチャットにだけする
    promise_check = (
        create_promise_check(settings.twitch, settings.jev, base_dir)
        if settings.twitch.enabled and settings.twitch.channel.strip()
        else None
    )
    # 返事が持っている物を「ない」と言っていないか（持ち物とチェストの事実と照らす）
    fact_check = (
        create_fact_check(settings.twitch, settings.jev, base_dir)
        if settings.twitch.enabled and settings.twitch.channel.strip()
        else None
    )
    llm = create_llm_service(
        character=settings.character,
        event_publisher=bus,
        conversation=conversation,
        objectives=game.objectives,
        generation_log=gemini_calls,
        llm=settings.llm,
        text_generator=shared_text,
        readings=readings,
        notes=game.notes,
        lessons=game.lessons,
        promise_check=promise_check,
        fact_check=fact_check,
        self_facts=self_facts,
    )
    closers: list[Any] = [promise_check.close] if promise_check is not None else []
    if fact_check is not None:
        closers.append(fact_check.close)

    async def stop_designing() -> None:
        game.objectives.cancel_designing()

    closers.append(stop_designing)

    tts = None
    if settings.stream.speak:
        from ailoveshen.factories.tts import (
            create_and_connect_tts_service,
            describe_engine,
            engine_of,
        )

        try:
            tts = await create_and_connect_tts_service(
                config=tts_config,
                event_publisher=bus,
                get_current_emotion=llm.get_current_emotion,
                pronounce=readings.apply,
                transliterate=ForeignSpeech(shared_text).pronounce,
            )
        except Exception as e:  # noqa: BLE001 - どの失敗でも理由を言って始めない
            await game.close()
            await llm.close()
            how = (
                "Irodori-TTS-Server を起動し（scripts/irodori/start_mac.sh、docs/setup/irodori_tts.md）、"
                "curl http://localhost:8088/health で確かめる。"
                if engine_of(tts_config) == "irodori"
                else "cd docker && docker compose up -d で起動し、"
                "curl http://localhost:5001/models/info で確かめる。"
            )
            raise StreamSetupError(
                f"TTS サーバー（{describe_engine(tts_config)}）につながらない: {e}。"
                f"{how}読み上げなしで配信するなら --no-speak"
            ) from e
        closers.append(tts.stop)
        logger.info(f"[tts] 読み上げる（{describe_engine(tts_config)}）")

    def activity():
        return game.session.activity() if game.session else None

    # 読み上げの感情は、読む前に Jev がアバターの表情と一緒に選ぶ（設計書 34 §4）
    from ailoveshen.application.use_cases.avatar_director import (
        AvatarReaction,
        LineReactions,
        voice_emotion,
    )
    from ailoveshen.factories.avatar import create_avatar_director
    from ailoveshen.presentation.web.avatar import emotion_from_text

    reactions = LineReactions()
    voice_director = None
    if tts is not None and str(tts_config.get("emotion_judge", "jev")) == "jev":
        voice_director = create_avatar_director(
            settings.avatar, settings.jev, activity, base_dir=base_dir
        )
        if voice_director is not None:
            closers.append(voice_director.close)
            logger.info("[tts] 読み上げの感情は Jev が選ぶ")

    async def voiced(text: str):
        """読む前に選ぶ感情（選べなければ None: 中立のまま）。"""
        if voice_director is None:
            return None
        rule = emotion_from_text(text)
        reaction = await voice_director.react(
            "speaking", text, AvatarReaction(emotion=rule.value if rule else None)
        )
        reactions.remember(text, reaction)
        return voice_emotion(reaction)

    # 直前に話した行: 実況は同じ内容を繰り返さない（設計書 43）
    from ailoveshen.presentation.services.live_narration import LiveNarration, SpokenLines

    spoken = SpokenLines()

    # 読み上げた文と、そのときの状態（設計書 65 §2）
    spoken_log = None
    if settings.stream.spoken_record_dir:
        from ailoveshen.infrastructure.adapters.storage.jsonl_watch_recorder import (
            JsonlWatchRecorder as SpokenRecorder,
        )
        from ailoveshen.presentation.services.spoken_log import SpokenLog

        spoken_log = SpokenLog(
            SpokenRecorder(str(base_dir / settings.stream.spoken_record_dir)), activity
        )
        if tts is not None:
            tts.on_outcome = spoken_log.tts

    def log_line(text: str, source: str, outcome: str = "said") -> None:
        if spoken_log is not None:
            spoken_log.line(text, source, outcome)

    # 話したつもりで、視聴者に聞こえなかった文（直前と同じで言わない、読み上げが古くなって捨てた）は、
    # Narrator に知らせる: そこで話した予告は、話していないことになる（設計書 65 §3）
    unheard: list[Callable[[str], None]] = []

    def not_heard(text: str) -> None:
        for forget in unheard:
            forget(text)

    if tts is not None:
        recorded = tts.on_outcome

        def on_tts_outcome(request: Any, outcome: str, waited: float) -> None:
            if recorded is not None:
                recorded(request, outcome, waited)
            if outcome != "played":
                not_heard(request.text)

        tts.on_outcome = on_tts_outcome

    async def say(text: str) -> None:
        same = spoken.repeats(text)
        if same is not None:
            logger.info(f"[say] 直前と同じ内容なので言わない: {text}（前: {same}）")
            log_line(text, "commentary", "repeat")
            not_heard(text)
            return
        spoken.remember(text)
        logger.info(f"[say] {text}")
        log_line(text, "commentary")
        if tts is not None:
            await tts.speak(text, emotion=await voiced(text), source="commentary")

    async def say_notice(text: str) -> None:
        """定型の一言（考え始めた）。同じ文を何度も言うので、繰り返しの判定は通さない。"""
        spoken.remember(text)
        logger.info(f"[say] {text}")
        log_line(text, "notice")
        if tts is not None:
            await tts.speak(text, emotion=await voiced(text), source="commentary")

    async def say_reply(text: str, source: str = "reply") -> None:
        spoken.remember(text)
        log_line(text, source)
        if tts is not None:
            # 視聴者への返事は実況より先に読む
            await tts.speak(
                text, priority=SpeechPriority.HIGH, emotion=await voiced(text), source="chat"
            )

    # 返事のあとに裏で設計していた建物が断られたら、頼んだ人に言う（docs/design/107。固定の一言）
    async def design_failed(user_name: str, title: str, reason: str) -> None:
        text = f"{user_name}、ごめんね、「{title}」の設計がうまくいかなかったから、あきらめるね。また頼んでね！"
        conversation.add_streamer_message(text, MessageType.RESPONSE, addressed_to=user_name)
        await say_reply(text)

    game.objectives.on_design_failed = design_failed

    # 管理画面（設計書 54）で喋らせる文。実況の重複の判定は通さない（言わせたいことを言う）。読み上げは
    # 待たない（長い文で管理画面の操作が固まらない）
    background: set[asyncio.Task[Any]] = set()

    def spawn(work) -> None:
        task = asyncio.ensure_future(work)
        background.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            background.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.opt(exception=t.exception()).warning("管理画面で頼んだ読み上げに失敗した")

        task.add_done_callback(done)

    async def announce(text: str) -> bool:
        conversation.add_streamer_message(text, MessageType.COMMENTARY)
        if tts is None:
            return False
        spoken.remember(text)
        spawn(say_reply(text, "admin"))
        return True

    async def instruct(text: str, speak_reply: bool) -> str:
        """配信者の指示として渡す（視聴者の頼みと同じ仕組み。上限なしで大目標に、「今」なら決め直す）。"""
        await game.refresh()
        reply = await llm.generate_response(
            PRODUCER_NAME, text, session=game.session, producer=True
        )
        if reply and speak_reply and tts is not None:
            spawn(say_reply(reply, "admin"))
        return reply

    def rethink(reason: str) -> bool:
        session = game.session
        if session is None:
            return False
        session.request_rethink(
            "the director asked you to decide the small goal again"
            + (f": {reason}" if reason else "")
        )
        return True

    async def reset_goals() -> bool:
        """大目標を最初のリストに戻し、今の小目標を切って決め直させる（管理画面）。"""
        session = game.session
        if session is None:
            return False
        await game.objectives.reset(session.plan)
        session.request_rethink("the director reset every objective to the starting list")
        return True

    # 小目標の切れ目の実況は、今話す価値があるかを Jev が決める（設計書 34 §6）
    gate = None
    live_judge = None
    if settings.jev.api_key:
        from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge

        live_judge = JevFastJudge(
            api_key=settings.jev.api_key,
            model=settings.jev.model,
            timeout_seconds=settings.jev.timeout_seconds,
        )
        if settings.stream.commentary_judge == "jev":
            from ailoveshen.application.use_cases.commentary_gate import CommentaryGate
            from ailoveshen.infrastructure.adapters.storage.jsonl_watch_recorder import (
                JsonlWatchRecorder,
            )

            gate = CommentaryGate(
                judge=live_judge,
                activity=activity,
                recorder=JsonlWatchRecorder(str(base_dir / settings.stream.commentary_record_dir))
                if settings.stream.commentary_record_dir
                else None,
            )
            closers.append(gate.close)
    # 読む前に、実況・一言を今の状態と照らす（設計書 65 §4）。Jev の判定器は間合いの判断と共有する
    guard = None
    if settings.stream.speech_check == "jev":
        if live_judge is not None:
            from ailoveshen.application.use_cases.speech_check import SpeechCheck
            from ailoveshen.presentation.services.speech_guard import SpeechGuard

            guard = SpeechGuard(SpeechCheck(live_judge), activity, spoken_log)
            logger.info("[say] 実況と一言は、読む前に Jev が今の状態と照らす")
        else:
            logger.warning("[say] Jev の鍵がないので、実況と一言を今の状態と照らさない")
    narrator = Narrator(
        llm,
        activity=activity,
        say=say,
        gate=gate.worth_speaking if gate is not None else None,
        max_silence_seconds=settings.stream.max_silence_seconds,
        notice=say_notice,
        guard=guard,
        remember=lambda text: conversation.add_streamer_message(text, MessageType.COMMENTARY),
    )
    narrator.subscribe(bus)
    unheard.append(narrator.unheard)

    live = None
    if settings.stream.live_interval_seconds > 0:
        live = LiveNarration(
            llm,
            activity=activity,
            say=say,
            spoken=spoken,
            is_idle=tts.is_idle if tts is not None else (lambda: True),
            interval_seconds=settings.stream.live_interval_seconds,
            judge=live_judge,
            idle_seconds=settings.stream.live_idle_seconds,
            thinking_seconds=settings.stream.live_thinking_seconds,
            guard=guard,
        )
        live.subscribe(bus)
        logger.info(f"[live] {settings.stream.live_interval_seconds:.0f} 秒黙っていたら一言話す")

    board = None
    if settings.stream.board_port:
        from ailoveshen.factories.avatar import create_avatar_stage
        from ailoveshen.presentation.web.goal_board import GoalBoard

        avatar = create_avatar_stage(
            settings.avatar,
            settings.jev,
            activity,
            base_dir=base_dir,
            reactions=reactions if voice_director is not None else None,
        )
        avatar.subscribe(bus)
        closers.append(avatar.close)
        from ailoveshen.presentation.web.admin import AdminPanel

        board = GoalBoard(
            activity,
            gemini_calls=gemini_calls.recent,
            gemini_image=gemini_calls.image,
            avatar=avatar,
            readings=readings.all,
            admin=AdminPanel(
                SpeechPresets(JsonPresetStore(base_dir / settings.stream.presets_path)),
                speak=announce,
                instruct=instruct,
                rethink=rethink,
                reset=reset_goals,
                lessons=game.lessons,
            ),
        )
        board.subscribe(bus)

    chat = responder = None
    twitch = settings.twitch
    if twitch.enabled and twitch.channel.strip():
        from ailoveshen.infrastructure.adapters.twitch import TwitchIrcChat

        # トークンがあれば、そのアカウントでログインしてチャットに書ける（!commands の一覧）
        chat = TwitchIrcChat(twitch.channel, login=twitch.bot_login, token=twitch.access_token)
        # 返事の前に Jev が仕分ける（設計書 34 §5）
        triage = create_comment_triage(twitch, settings.jev, activity, base_dir)
        if triage is not None:
            closers.append(triage.close)
        responder = ChatResponder(
            llm,
            session=lambda: game.session,
            say=say_reply,
            min_interval_seconds=twitch.min_interval_seconds,
            backlog=twitch.backlog,
            readings=readings,
            chat=chat,
            # 配信者自身と書き込むアカウントのコメントには返事をしない（コマンドは実行する）。
            # ボット（StreamElements など）には何も反応しない
            quiet=(chat.channel, chat.login),
            ignore=twitch.ignore_users,
            refresh=game.refresh,
            triage=triage.triage if triage is not None else None,
            remember=lambda c: conversation.add_viewer_message(
                c.message, c.user_name, c.user_id, c.received_at
            ),
        )
        how = f"{chat.login} で書き込みもする" if chat.login else "読むだけ"
        logger.info(f"[chat] Twitch #{chat.channel} のチャットを読む（{how}）")
    else:
        logger.info("[chat] Twitch のチャットは読まない（.env の TWITCH_CHANNEL が空か、オフ）")

    # 配信への応援（フォロー、サブスク、ギフト、ビッツ、レイド）: お礼を言い、アバターも動く（設計書 71）。
    # サブスク・ギフト・ビッツ・レイドはチャットの接続から、フォローは EventSub（トークンが要る）
    supports: tuple[Any, ...] = ()
    thanker = None
    if chat is not None and twitch.support.enabled:
        sources: list[Any] = [chat]
        if twitch.support.follows:
            from ailoveshen.infrastructure.adapters.twitch import TwitchEventSub
            from ailoveshen.infrastructure.adapters.twitch.twitch_auth import FOLLOW_SCOPES, build_token_source

            async def say_authorize() -> None:
                await say_notice("Twitch の許可が必要だよ。ブラウザに出た画面で、許可を押してね")

            # 手で書いたトークンがあればそのまま、なければ Client-Id から自動で取って更新する
            # （許可が要るときは、ブラウザで許可の画面を開き、声で知らせる）
            token_source = build_token_source(
                twitch.client_id,
                twitch.eventsub_token,
                twitch.token_path,
                FOLLOW_SCOPES,
                client_secret=twitch.client_secret,
                open_url=webbrowser.open if twitch.open_browser else None,
                notify=say_authorize,
            )
            if token_source is not None:
                sources.append(TwitchEventSub(twitch.channel, twitch.client_id, token_source))
            else:
                logger.warning(
                    "[support] フォロー通知は受けない（TWITCH_CLIENT_ID が要る。許可は初回の起動でログに出る URL を"
                    "開くだけ: docs/streaming.md）"
                )

        async def say_support(text: str) -> None:
            spoken.remember(text)
            logger.info(f"[say] {text}")
            log_line(text, "support")
            conversation.add_streamer_message(text, MessageType.COMMENTARY)
            if tts is not None:
                # 応援へのお礼は実況より先に、喜んだ声で（Jev の判断は待たない）
                await tts.speak(
                    text,
                    priority=SpeechPriority.HIGH,
                    emotion=EmotionState(EmotionType.HAPPY, 0.9),
                    source="support",
                )

        thanker = SupportResponder(
            say=say_support,
            publish=bus.publish,
            min_interval_seconds=twitch.support.min_interval_seconds,
            batch_seconds=twitch.support.batch_seconds,
            backlog=twitch.support.backlog,
            dedupe_seconds=twitch.support.dedupe_seconds,
            # 配信者自身と書き込むアカウント、ボットの応援には何も言わない
            ignore=(chat.channel, chat.login, *twitch.ignore_users),
        )
        supports = tuple(sources)
        kinds = "サブスク・ギフト・ビッツ・レイド" + ("・フォロー" if len(sources) > 1 else "")
        logger.info(f"[support] {kinds}にお礼を言う")

    stream = Stream(
        game,
        llm,
        board=board,
        board_port=settings.stream.board_port,
        chat=chat,
        responder=responder,
        closers=tuple(closers),
        live=live,
        supports=supports,
        thanker=thanker,
    )
    stream.subscribe(bus)
    logger.info(f"[control] {settings.minecraft.control}")
    return stream


def _check_port_free(port: int, host: str = "127.0.0.1") -> None:
    """目標ボードのポートが空いているか（使われていると OBS のソースが空のまま配信が進む）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as e:
            raise StreamSetupError(
                f"目標ボードのポート {port} が使われている（{e.strerror}）。前の配信や "
                "examples/integration_test_minecraft.py が動いていないか確かめる。"
                "別のポートなら --board-port（OBS のブラウザソースの URL も合わせる）"
            ) from e
