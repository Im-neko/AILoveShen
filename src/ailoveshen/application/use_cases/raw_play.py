"""
判断なしのプレイ（実験。設計書 191）: `minecraft.agent.control: raw`。

ブリッジは何も判断しない: 目標も、その判定も、ソルバーの提案も、候補も、定石も、助言もない。LLM は、測ったままの状態
（`raw_state`）と課題の文を見て、体を動かす技（生の bot の JavaScript）を自分で書き、試し、直す。使える道具は技の 3 つ
（run_skill / try_skill / write_skill）と、ゲームの知識・断面の調べものだけ。近くの敵への反射だけは、ブリッジが（LLM の外で）
働く。食べる・火や水を避ける・夜を越すといった生き延びる手も、LLM が技にする。

通常のプレイ（`AdvancePlayUseCase`）の目標の決定・停滞や行き詰まりの検知・Jev・教訓・覚えた候補は、ここには入らない。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import replace
from typing import Awaitable, Callable, Optional, TypeVar

from loguru import logger

from ailoveshen.application.dto.game_dto import PlayStepReport
from ailoveshen.application.ports.input.play import IAdvancePlay, IStartPlay
from ailoveshen.application.ports.output.game_prompt_builder import IGamePromptBuilder
from ailoveshen.application.ports.output.minecraft_bridge import IMinecraftBridge
from ailoveshen.application.ports.output.text_generator import ITextGenerator
from ailoveshen.application.use_cases.play import (
    MAX_QUERIES_PER_STEP,
    RECENT_TOOLS,
    REFLEX_POLL_SECONDS,
    REFLEX_RELEASE_SECONDS,
    REFLEX_WAIT_LIMIT_SECONDS,
    SKILL_NOTE_SECONDS,
    TRY_OK_NEXT,
)
from ailoveshen.application.use_cases.skill_feedback import SkillHistory
from ailoveshen.application.use_cases.skill_progress import skill_note, start_position
from ailoveshen.application.use_cases.skills import NotWritten, SkillWriter, order_skills
from ailoveshen.application.use_cases.advisor import Advisor
from ailoveshen.application.use_cases.guides import GuideBook
from ailoveshen.application.use_cases.raw_notes import NoteBook
from ailoveshen.application.use_cases.tool_catalog import (
    ASK_ADVISOR,
    NOTE_TOOLS,
    RAW_GUIDE_TOOLS,
    RAW_QUERY_TOOLS,
    TRY_SKILL,
    WRITE_NOTE,
    WRITE_SKILL,
    ToolCallError,
    is_skill_tool,
    parse_tool_call,
    raw_tool_specs,
)
from ailoveshen.domain.entities import ObjectivePlan, PlaySession
from ailoveshen.domain.exceptions import GameBridgeError
from ailoveshen.domain.value_objects import ActionDecision, SkillRun, ToolCall, ToolOutcome

T = TypeVar("T")

# 状態から外すもの: 反射は待つために読み、技は動かしている間に読む。選ぶ材料ではない
HIDDEN_FROM_PROMPT = ("reflex", "skill")
# 技を書く・試す回数の上限（費用の上限）は、通常のプレイより大きくする: 試して直すのが、この実験で測るもの
RAW_SKILL_REWRITES_PER_HOUR = 60
RAW_SKILL_TRIES_PER_HOUR = 120
RAW_SKILL_MAX_ATTEMPTS = 3
# 聞くかどうかを LLM に任せると、死んだ後も夜の前も聞かなかった（設計書 197 §7）ので、この 2 つの場面ではコードが聞く
DEATH_QUESTION = (
    "死んだ（「いま」の deaths の last が最後の様子）。持ち物は全部失った。同じ死に方を繰り返さないために、"
    "生き返った今から何を、どの順でするか。書くべき技（何をする技か）と、メモに残すべき学びも挙げて"
)
NIGHT_QUESTION = (
    "もうすぐ夜になる（敵が湧き続けて、外にいると死ぬ）。夜を越えるために、今のうちに何をするか。"
    "夜の間どこで何をするか、そのために書くべき技（何をする技か）まで具体的に"
)
NIGHT_FALLS = ("dusk", "night")


class RawStartPlay(IStartPlay):
    """家の設計も街の準備もしない。からのセッションを返すだけ。"""

    def __init__(self, plan: ObjectivePlan) -> None:
        self._plan = plan

    async def execute(self) -> PlaySession:
        return PlaySession(blueprint=None, plan=self._plan)


class RawAdvancePlay(IAdvancePlay):
    """1 ステップ = 道具を 1 つ選んで実行する（調べものは同じステップの中で選び直す）。"""

    def __init__(
        self,
        bridge: IMinecraftBridge,
        text_generator: ITextGenerator,
        prompt_builder: IGamePromptBuilder,
        skills: SkillWriter,
        task: str,
        guides: GuideBook | None = None,
        advisor: Advisor | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bridge = bridge
        self._generator = text_generator
        self._prompts = prompt_builder
        self._skills = skills
        self._task = task
        self._guides = guides or GuideBook()
        self._notes = NoteBook()
        self._advisor = advisor
        self._deaths_seen = 0
        self._phase_seen: Optional[str] = None
        self._sleep = sleep
        self._recent: deque[ToolOutcome] = deque(maxlen=RECENT_TOOLS)
        self._skill_failures: dict[str, str] = {}
        self._skill_history = SkillHistory()

    async def execute(self, session: PlaySession) -> PlayStepReport:
        await self._wait_out_reflex()
        skills = await self._skill_list()
        specs = raw_tool_specs(advisor=self._advisor is not None)
        outcome: Optional[ToolOutcome] = None
        for attempt in range(MAX_QUERIES_PER_STEP + 1):
            state = await self._bridge.raw_state()
            prompt = self._prompt(state, skills)
            if attempt == 0 and (question := self._consultation(state)):
                await self._consult(question, prompt)
                prompt = self._prompt(state, skills)
            last = self._recent[-1] if self._recent else None
            purpose = "tool_after_failure" if last is not None and not last.ok else "tool"
            offered = (
                specs
                if attempt < MAX_QUERIES_PER_STEP
                else [t for t in specs if t.name not in (*RAW_QUERY_TOOLS, *RAW_GUIDE_TOOLS, *NOTE_TOOLS, ASK_ADVISOR)]
            )
            choice = await self._generator.choose_tool(prompt, offered, purpose=purpose)
            try:
                call = parse_tool_call(choice)
            except ToolCallError as e:
                outcome = ToolOutcome(
                    call=ToolCall(name=choice.name or "?", args=dict(choice.args)),
                    ok=False,
                    result=f"invalid call: {e}{self._skill_name_hint(choice.name, skills)}",
                    seconds=0.0,
                    refused=True,
                )
                self._recent.append(outcome)
                break
            if call.name in RAW_GUIDE_TOOLS:
                ok, result = self._guides.read(str(call.args.get("name") or ""))
                self._recent.append(ToolOutcome(call=call, ok=ok, result=result, seconds=0.0, refused=not ok))
                logger.info(f"手引きを読んだ: {call.describe()}")
                continue
            if call.name == WRITE_NOTE:
                replace = call.args.get("replace")
                ok, result = self._notes.write(
                    str(call.args.get("text") or ""), int(replace) if replace is not None else None
                )
                self._recent.append(ToolOutcome(call=call, ok=ok, result=result, seconds=0.0, refused=not ok))
                logger.info(f"メモ: {call.args.get('text')} -> {result}")
                continue
            if call.name == ASK_ADVISOR:
                if self._advisor is None:
                    self._recent.append(
                        ToolOutcome(call=call, ok=False, result="invalid call: no advisor here", seconds=0.0, refused=True)
                    )
                    break
                ok, result = await self._advisor.ask(str(call.args.get("question") or ""), prompt)
                self._recent.append(ToolOutcome(call=call, ok=ok, result=result, seconds=0.0, refused=not ok))
                continue
            if call.name in RAW_QUERY_TOOLS:
                ok, result, seconds, refused = await self._bridge.run_tool(call.name, call.args)
                self._recent.append(
                    ToolOutcome(call=call, ok=ok, result=result, seconds=seconds, refused=refused)
                )
                logger.info(f"調べた: {call.describe()} -> {result[:200]}")
                continue
            if not is_skill_tool(call.name):
                outcome = ToolOutcome(
                    call=call,
                    ok=False,
                    result="invalid call: only skills can move the body here; write one with try_skill",
                    seconds=0.0,
                    refused=True,
                )
                self._recent.append(outcome)
                break
            session.start_action(call.intent, call.intent)
            logger.info(f"道具: {call.describe()}「{call.intent}」")
            try:
                outcome = await self._skill_step(session, call, skills)
            finally:
                session.end_action()
            self._recent.append(outcome)
            break
        if outcome is None:  # 調べものだけで上限に届いた（最後の周では調べものを出さないので、ふつうは来ない）
            outcome = self._recent[-1]
        return PlayStepReport(
            goal=None,
            goal_changed=False,
            status=None,
            decision=ActionDecision(action_id=outcome.call.describe(), confidence=1.0),
            result=outcome.as_action_result(),
            house_complete=False,
        )

    @staticmethod
    def _skill_name_hint(name: str | None, skills: list) -> str:
        """技の名前を道具として呼んだとき、呼び方を教える（「知らない道具」だけでは、同じ呼び方を何十回も繰り返した）。"""
        if not any(s.name == name for s in skills):
            return ""
        return f". {name} is a saved skill, not a tool: call run_skill with name={name} (and args)"

    def _prompt(self, state: dict, skills: list) -> str:
        return self._prompts.build_raw_prompt(
            self._task,
            {k: v for k, v in state.items() if k not in HIDDEN_FROM_PROMPT},
            tuple(self._recent),
            skills,
            self._guides.guides,
            self._notes.notes,
        )

    def _consultation(self, state: dict) -> str:
        """
        advisor に自動で聞く場面なら、その質問。聞く先が無いとき・場面でないときは空。
        死んだ直後（deaths が増えた）と、夜に入るとき（昼や夜明けから dusk / night へ変わった）。
        """
        deaths = int((state.get("deaths") or {}).get("count") or 0)
        phase = state.get("time")
        died, self._deaths_seen = deaths > self._deaths_seen, deaths
        night_falls = phase in NIGHT_FALLS and self._phase_seen is not None and self._phase_seen not in NIGHT_FALLS
        self._phase_seen = phase
        if self._advisor is None:
            return ""
        return DEATH_QUESTION if died else NIGHT_QUESTION if night_falls else ""

    async def _consult(self, question: str, prompt: str) -> None:
        """質問を advisor に渡し、答えを直近の道具に載せる（LLM が自分で聞いたときと同じ形）。"""
        assert self._advisor is not None
        ok, result = await self._advisor.ask(question, prompt)
        logger.info(f"advisor に自動で聞いた: {question[:40]}… -> {result[:200]}")
        self._recent.append(
            ToolOutcome(call=ToolCall(name=ASK_ADVISOR, args={"question": question}), ok=ok, result=result, seconds=0.0)
        )

    async def _wait_out_reflex(self) -> None:
        """反射（近くの敵への対処）が動いている間は、技も断られる。終わるまで選ばせずに待つ。"""
        state = await self._bridge.raw_state()
        waited = 0.0
        while state.get("reflex"):
            if waited >= REFLEX_WAIT_LIMIT_SECONDS:
                logger.warning(f"反射が {REFLEX_WAIT_LIMIT_SECONDS} 秒続いている: 手放して、選ばせる")
                await self._bridge.release_reflex(REFLEX_RELEASE_SECONDS)
                return
            await self._sleep(REFLEX_POLL_SECONDS)
            waited += REFLEX_POLL_SECONDS
            state = await self._bridge.raw_state()

    async def _skill_list(self) -> list:
        try:
            # 並びは名前順: 成績順だと、成功のたびに入れ替わって、プロンプトキャッシュの先頭一致が壊れる（設計書 198）
            return sorted(order_skills(await self._bridge.skills(), None), key=lambda s: s.name)
        except GameBridgeError as e:
            logger.warning(f"技の一覧を読めなかった: {e}")
            return []

    async def _skill_step(self, session: PlaySession, call: ToolCall, skills: list) -> ToolOutcome:
        """技を書いて試す・書いて保存して試す・覚えた技を動かす（設計書 22 §4、185）。"""
        name = call.args["name"]
        if call.name not in (TRY_SKILL, WRITE_SKILL):
            known = next((s for s in skills if s.name == name), None)
            return await self._run(
                session,
                call,
                lambda: self._bridge.run_skill(name, call.args.get("args", {}), call.args.get("version")),
                known.description if known else name,
            )
        unsaved = call.name == TRY_SKILL
        written = await self._skills.write(
            name,
            call.args["what"],
            session.activity(),
            tuple(self._recent),
            skills,
            self._skill_failures.get(name, ""),
            unsaved=unsaved,
        )
        if isinstance(written, NotWritten):
            logger.info(f"技を書けなかった: {name}: {written.why}")
            return ToolOutcome(call=call, ok=False, result=written.why, seconds=0.0, refused=True)
        self._skill_history.rewritten(name)
        if unsaved:
            assert written.draft is not None
            return await self._run(
                session,
                call,
                lambda: self._bridge.try_skill(written.draft, written.trial_args),
                name,
                trial_args=written.trial_args,
            )
        outcome = await self._run(
            session,
            call,
            lambda: self._bridge.run_skill(name, written.trial_args, written.version),
            written.description,
        )
        return replace(outcome, result=f"wrote {name} v{written.version}; trial: {outcome.result}")

    async def _run(
        self,
        session: PlaySession,
        call: ToolCall,
        start: Callable[[], Awaitable[SkillRun]],
        description: str,
        trial_args: Optional[dict] = None,
    ) -> ToolOutcome:
        """技を 1 回動かして結果にする。`trial_args` があれば保存しない試し。"""
        trial = trial_args is not None
        name = call.args["name"]
        run = replace(await self._narrated(session, name, start()), name=name)
        logger.info(f"技{'を試した' if trial else ''}: {run.describe()}（{run.seconds}秒、道具 {len(run.calls)}）")
        text = run.describe()
        if trial:
            effect = run.trial_effect()
            text += f"; {effect}" if effect else ""
        if run.ok:
            self._skill_failures.pop(name, None)
            if trial:
                self._skills.mark_proven(name, trial_args)
                text += TRY_OK_NEXT
        elif run.ended != "refused":  # 動かさなかった断りは、直すときに見せる失敗ではない
            self._skill_failures[name] = run.describe()
        # 直すか、やり直すかの材料（技の成績、失敗の種類）。raw には見張りがなく、止まるのは上限か反射（設計書 194）
        text += self._skill_history.feedback(name, run, trial, await self._phase() if run.ended == "stopped" else "day")
        return ToolOutcome(call=call, ok=run.ok, result=text, seconds=run.seconds)

    async def _phase(self) -> str:
        """今の時間帯（day / dusk / night / dawn）。測れなければ day（夜の案内を出さない）。"""
        try:
            return str((await self._bridge.raw_state()).get("time") or "day")
        except GameBridgeError:
            return "day"

    async def _narrated(self, session: PlaySession, name: str, work: Awaitable[T]) -> T:
        """技が動いている間、経過を session.skill_note に入れる（実況が途中経過を話す材料。設計書 186）。"""
        try:
            start = start_position(await self._bridge.raw_state())
        except GameBridgeError:
            start = None
        poller = asyncio.create_task(self._poll(session, name, start))
        try:
            return await work
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            session.skill_note = ""

    async def _poll(
        self, session: PlaySession, name: str, start: Optional[tuple[float, float, float]]
    ) -> None:
        while True:
            await self._sleep(SKILL_NOTE_SECONDS)
            try:
                state = await self._bridge.raw_state()
            except GameBridgeError:
                continue
            session.skill_note = skill_note(name, state, start)
