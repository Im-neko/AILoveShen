"""行動の途中の一言のテスト（docs/design/43_live_narration.md）。"""

from unittest.mock import AsyncMock

import pytest

from ailoveshen.domain.events import GameActionExecutedEvent
from ailoveshen.domain.value_objects import (
    Activity,
    FastAnswer,
    FastVerdict,
    GameObservation,
    Goal,
    GoalPredicate,
    GoalSpec,
)
from ailoveshen.presentation.services.live_narration import LiveNarration, SpokenLines, similarity

GOAL = Goal(GoalSpec(GoalPredicate.HAVE, item="log", count=16), "木を集める")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _obs(inventory, x=0.0, health=20, phase="day", inside=False):
    return GameObservation(
        state={"inventory": inventory, "self": {"position": {"x": x, "y": 64, "z": 0}}},
        candidates=(),
        health=health,
        food=20,
        time_phase=phase,
        inside_home=inside,
    )


def _setup(lines=(), intent="北の森で原木を集めてくるね", obs=None):
    clock = Clock()
    spoken = SpokenLines(clock=clock)
    said = []
    tts = {"busy": False}

    async def say(text):
        if spoken.repeats(text) is None:  # factories/stream.py の say と同じ
            spoken.remember(text)
            said.append(text)

    llm = AsyncMock()
    llm.generate_commentary.side_effect = list(lines)
    state = {"activity": Activity(acting=True, goal=GOAL, intent=intent, observation=obs or _obs({}))}
    live = LiveNarration(
        llm,
        activity=lambda: state["activity"],
        say=say,
        spoken=spoken,
        is_idle=lambda: not tts["busy"],
        interval_seconds=10,
        clock=clock,
    )
    return live, clock, said, tts, llm, state


def test_similar_lines_are_the_same_content():
    assert similarity("原木を集めてくるね！", "原木を集めてくるね") == 1.0
    assert similarity("北の森で原木を集めてくるね", "南の洞窟で鉄を掘るよ") < 0.2


@pytest.mark.asyncio
async def test_jev_semantic_duplicate_is_blocked():
    """Jev が「同じ意味」なら、表記の揺れでも発話を抑止する。"""

    class FakeJudge:
        async def ask(self, state, questions):
            return FastVerdict(
                answers={questions[0].name: FastAnswer(questions[0].name, True, 0.9)},
                elapsed_ms=10,
                input_tokens=20,
            )

    live, clock, said, _, llm, state = _setup(
        lines=["また拒否されちゃった……！ 目の前にベッドがあるのに家に入れないの～？"],
        intent="目の前にベッドがあるのに家に入れない",
    )
    live._judge = FakeJudge()

    live._spoken.remember("目の前にベッドがあるのに家に入れない")
    assert await live._duplicate_of_recent("また拒否されちゃった……！ 目の前にベッドがあるのに家に入れないの～？") == (
        "目の前にベッドがあるのに家に入れない"
    )
    assert llm.generate_commentary.await_count == 0

    state["activity"] = Activity(
        acting=True,
        goal=GOAL,
        intent="目の前にベッドがあるのに、なぜか家に戻ろうとして拒否されちゃう……！",
        observation=_obs({}),
    )
    clock.now = 20
    await live.tick()
    assert said == []
    assert llm.generate_commentary.await_count == 0


@pytest.mark.asyncio
async def test_quiet_is_measured_from_the_end_of_speech():
    """読み上げている間は数えない。終わってから 10 秒で話す（新しい intent は LLM なしで）。"""
    live, clock, said, tts, llm, state = _setup()
    tts["busy"] = True
    clock.now = 30
    await live.tick()  # 話している
    tts["busy"] = False
    clock.now = 35
    await live.tick()
    assert said == []  # 終わってから 5 秒
    clock.now = 40
    await live.tick()
    assert said == []  # 基準ができただけ（状況が変わるまで黙る）
    state["activity"] = Activity(
        acting=True, goal=GOAL, intent="北の森で原木を集めてくるね", observation=_obs({"oak_log": 1})
    )
    clock.now = 50
    await live.tick()
    assert said == ["北の森で原木を集めてくるね"]
    llm.generate_commentary.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_long_action_gets_new_lines_with_what_changed_and_no_repeats():
    """60 秒続く 1 つの行動: intent は 1 回だけ、あとは変わったことを渡す。似た行は言わない。"""
    live, clock, said, _, llm, state = _setup(
        lines=["原木が 4 本になった、あと少し！"]
    )
    await live.on_action(GameActionExecutedEvent(action_id="dig oak_log", ok=True, result="got"))
    for t, logs, x in [(10, 0, 0), (20, 0, 0), (30, 0, 12), (40, 4, 12)]:
        state["activity"] = Activity(
            acting=True,
            goal=GOAL, intent="北の森で原木を集めてくるね", observation=_obs({"oak_log": logs}, x)
        )
        clock.now = t
        await live.tick()
    # (10) 基準、(20) 変わらず黙る。(30) 12m 動いた: intent を 1 回、(40) 原木が増えた: 新しい一言
    assert said == [
        "北の森で原木を集めてくるね",
        "原木が 4 本になった、あと少し！",
    ]
    last = llm.generate_commentary.await_args_list[-1].kwargs
    assert last["purpose"] == "live" and last["record"] is False
    events = last["recent_events"]
    assert "行動の結果: dig oak_log → 成功: got" in events
    assert "持ち物の増減: oak_log +4" in events
    assert "北の森で原木を集めてくるね" in events[-1]  # 直前に話したこととは違う内容に
    # 正しいのは今の状態（直前の誤り「家の中に入った」を、外にいるのに繰り返した）。「予定を理由なく
    # 変えない」の指示は、状態より直前の誤りを優先させたので消した（設計書 65 §5: 食い違いはコードが見る）
    assert "正しいのは今の状態" in events[-1]
    assert "予定を理由なく変えない" not in events[-1]
    assert events[0] == f"今の小目標: {GOAL.describe()}（{GOAL.reason}）"


@pytest.mark.asyncio
async def test_a_line_made_while_someone_started_speaking_is_dropped():
    live, clock, said, tts, llm, _ = _setup(lines=["今日はいい天気だね"], intent="dig oak_log")

    async def slow(**kwargs):
        tts["busy"] = True  # 作っている間に返事が始まった
        return "今日はいい天気だね"

    llm.generate_commentary.side_effect = slow
    clock.now = 10
    await live.tick()
    assert said == []


@pytest.mark.asyncio
async def test_nothing_while_not_playing_and_the_english_ids_of_jev_are_not_read():
    live, clock, said, _, llm, state = _setup(
        lines=["よし、掘るよ！"], intent="dig oak_log at 1,2,3"
    )
    state["activity"] = None
    clock.now = 10
    await live.tick()
    assert said == []
    state["activity"] = Activity(acting=True, goal=GOAL, intent="dig oak_log at 1,2,3", observation=_obs({}))
    clock.now = 20
    await live.tick()  # 基準
    state["activity"] = Activity(
        acting=True, goal=GOAL, intent="dig oak_log at 1,2,3", observation=_obs({"oak_log": 1})
    )
    clock.now = 30
    await live.tick()
    assert said == ["よし、掘るよ！"]  # id はそのまま読まず、一言を作らせる


@pytest.mark.asyncio
async def test_nothing_is_said_while_the_next_action_is_being_chosen():
    """目標の決定を 4 分待つ間、終わった行動から「パンにするね」を何度も話した（2026-09-28）。"""
    live, clock, said, _, llm, state = _setup(lines=["パンにして空腹を満たすね"])
    state["activity"] = Activity(goal=GOAL, intent="", observation=_obs({}))
    for t in (10, 20, 30, 40):
        clock.now = t
        await live.tick()
    assert said == []
    llm.generate_commentary.assert_not_awaited()


def _acting(obs, failing=0):
    return Activity(acting=True, goal=GOAL, intent="北の森で原木を集めてくるね", observation=obs, failing=failing)


@pytest.mark.asyncio
async def test_nothing_is_said_while_nothing_has_changed():
    """状況が変わらない間は、何秒たっても黙る（2026-09-29: 頻度を下げる）。"""
    live, clock, said, _, llm, state = _setup(lines=["何か話す"])
    for t in (10, 20, 60, 120, 600):
        clock.now = t
        await live.tick()
    assert said == []
    llm.generate_commentary.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        _obs({}, health=15),
        _obs({}, phase="dusk"),
        _obs({}, inside=True),
        _obs({}, x=11),
        _obs({"stick": 2}),
    ],
    ids=["health", "time of day", "in the home", "moved", "items"],
)
async def test_each_kind_of_change_makes_a_line(changed):
    live, clock, said, _, llm, state = _setup(lines=["変わったね"])
    state["activity"] = _acting(_obs({}))
    clock.now = 10
    await live.tick()  # 基準
    state["activity"] = _acting(changed)
    clock.now = 20
    await live.tick()
    assert said == ["北の森で原木を集めてくるね"]  # 新しい intent はそのまま


@pytest.mark.asyncio
async def test_nothing_is_said_while_the_small_goal_keeps_failing():
    """スタックして動けていない間、似た一言を言い続けない（2026-09-29）。"""
    live, clock, said, _, llm, state = _setup(lines=["また失敗した", "逃げるね"])
    state["activity"] = _acting(_obs({}))
    clock.now = 10
    await live.tick()  # 基準
    for t, inventory in [(20, {"a": 1}), (30, {"a": 2}), (40, {"a": 3})]:
        state["activity"] = _acting(_obs(inventory), failing=2)
        clock.now = t
        await live.tick()
    assert said == []
    llm.generate_commentary.assert_not_awaited()
    state["activity"] = _acting(_obs({"a": 3}), failing=0)  # 別の手でうまくいった
    clock.now = 50
    await live.tick()
    assert said == ["北の森で原木を集めてくるね"]


@pytest.mark.asyncio
async def test_small_talk_fills_a_long_quiet_when_nothing_changes():
    """夜の穴の中で朝を待つ間、状況は変わらない。idle_seconds 黙っていたら、状況を渡して雑談を作らせる。"""
    live, clock, said, _, llm, state = _setup(lines=["穴の中は静かで、ちょっと落ち着くなあ"], intent="")
    live._idle = 60
    clock.now = 10
    await live.tick()  # 基準
    clock.now = 50
    await live.tick()
    assert said == []  # 変わらず、まだ 60 秒黙っていない
    clock.now = 75
    await live.tick()
    assert said == ["穴の中は静かで、ちょっと落ち着くなあ"]
    kwargs = llm.generate_commentary.await_args.kwargs
    assert kwargs["purpose"] == "live" and kwargs["record"] is False
    events = kwargs["recent_events"]
    assert "状況は変わっていない" in events[-1] and "新しく約束しない" in events[-1]
    # 話したあとは、また idle_seconds 待つ
    clock.now = 100
    await live.tick()
    assert len(said) == 1


@pytest.mark.asyncio
async def test_small_talk_is_off_by_default_and_never_while_failing_or_not_acting():
    live, clock, said, _, llm, state = _setup(lines=["今日はいい天気だね"], intent="")
    clock.now = 10
    await live.tick()
    clock.now = 500
    await live.tick()
    assert said == []  # idle_seconds 0: 変わるまで黙る（従来どおり）
    live._idle = 60
    state["activity"] = Activity(acting=True, goal=GOAL, intent="", failing=2, observation=_obs({}))
    await live.tick()
    state["activity"] = Activity(acting=False, goal=GOAL, intent="", observation=_obs({}))
    await live.tick()
    assert said == [] and llm.generate_commentary.await_count == 0


@pytest.mark.asyncio
async def test_thinking_aloud_fills_the_wait_for_the_next_goal():
    """目標の決定や行き詰まりで 2〜6 分黙っていた（2026-09-30）: 独り言を話す。行動は言わない。"""
    live, clock, said, _, llm, state = _setup(lines=["どうしようかな、水の上は落ち着かないなあ"], intent="")
    live._thinking = 40
    state["activity"] = Activity(goal=GOAL, intent="北の森で原木を集めてくるね", observation=_obs({}))
    clock.now = 0
    await live.tick()  # 待ち始め
    clock.now = 30
    await live.tick()
    assert said == []  # まだ 40 秒待っていない
    clock.now = 45
    await live.tick()
    assert said == ["どうしようかな、水の上は落ち着かないなあ"]
    kwargs = llm.generate_commentary.await_args.kwargs
    assert kwargs["purpose"] == "live" and kwargs["record"] is False
    assert "考え中の独り言" in kwargs["recent_events"][-1] and "やっていると言わない" in kwargs["recent_events"][-1]
    assert "北の森" not in " ".join(kwargs["recent_events"])  # 選ばれていない行動は渡さない
    clock.now = 60
    await live.tick()
    assert len(said) == 1  # 話したあとは、また 40 秒待つ


@pytest.mark.asyncio
async def test_thinking_aloud_also_fills_a_stuck_small_goal_and_is_off_by_default():
    live, clock, said, _, llm, state = _setup(lines=["うーん、うまくいかないなあ"], intent="")
    state["activity"] = _acting(_obs({}), failing=2)
    clock.now = 500
    await live.tick()
    assert said == [] and llm.generate_commentary.await_count == 0  # thinking_seconds 0: 従来どおり黙る
    live._thinking = 40
    clock.now = 510
    await live.tick()  # 待ち始め
    clock.now = 600
    await live.tick()
    assert said == ["うーん、うまくいかないなあ"]


@pytest.mark.asyncio
async def test_thinking_aloud_needs_a_sustained_wait_not_a_short_gap_between_actions():
    """数秒の選び直し（行動と行動の間）では話さない。待ちが thinking_seconds 続いてから。"""
    live, clock, said, _, llm, state = _setup(lines=["どうしようかな"], intent="")
    live._thinking = 40
    clock.now = 0
    state["activity"] = _acting(_obs({}))
    await live.tick()
    for t in (50, 60, 70):  # 行動している。選び直しの隙間ごとに待ち始めが戻る
        clock.now = t
        state["activity"] = Activity(goal=GOAL, intent="", observation=_obs({}))
        await live.tick()
        clock.now = t + 1
        state["activity"] = _acting(_obs({}))
        await live.tick()
    assert said == [] and llm.generate_commentary.await_count == 0


class _Guard:
    """照合の代わり: 決めた文だけ通す。作り直す文を返す。"""

    def __init__(self, verdict):
        self.verdict = verdict
        self.seen = []

    async def verify(self, text, regenerate):
        self.seen.append(text)
        return await self.verdict(text, regenerate)


@pytest.mark.asyncio
async def test_a_generated_line_is_checked_before_it_is_said_but_a_tool_intent_is_not():
    async def pass_through(text, regenerate):
        return text

    live, clock, said, _, llm, state = _setup(lines=["ここは静かだね"])
    live._guard = _Guard(pass_through)
    live._mark(state["activity"])
    live._last_intent = "北の森で原木を集めてくるね"  # intent は読み終わった
    state["activity"] = Activity(
        acting=True, goal=GOAL, intent="北の森で原木を集めてくるね", observation=_obs({"oak_log": 4})
    )
    clock.now = 20
    await live.tick()

    assert said == ["ここは静かだね"]
    assert live._guard.seen == ["ここは静かだね"]

    live, clock, said, _, llm, state = _setup(lines=["使われない"], intent="北の森で原木を集めてくるね")
    live._guard = _Guard(pass_through)
    live._mark(state["activity"])
    state["activity"] = Activity(
        acting=True, goal=GOAL, intent="南の洞窟で鉄を掘るよ", observation=_obs({"oak_log": 4})
    )
    clock.now = 20
    await live.tick()

    assert said == ["南の洞窟で鉄を掘るよ"]  # 道具の intent は行動そのものなので、照らさない
    assert live._guard.seen == []


@pytest.mark.asyncio
async def test_a_line_that_fails_the_check_is_rewritten_from_the_same_events_or_dropped():
    calls = []

    async def rewrite(text, regenerate):
        calls.append(text)
        return await regenerate(["前の案は食い違っている"])

    live, clock, said, _, llm, state = _setup(lines=["家に入れたよ", "まだ外だよ"])
    live._guard = _Guard(rewrite)
    live._mark(state["activity"])
    live._last_intent = "北の森で原木を集めてくるね"
    state["activity"] = Activity(
        acting=True, goal=GOAL, intent="北の森で原木を集めてくるね", observation=_obs({"oak_log": 4})
    )
    clock.now = 20
    await live.tick()

    assert calls == ["家に入れたよ"] and said == ["まだ外だよ"]
    first, second = llm.generate_commentary.await_args_list
    assert second.kwargs["recent_events"] == first.kwargs["recent_events"] + ["前の案は食い違っている"]
    assert second.kwargs["purpose"] == "live" and second.kwargs["record"] is False

    async def drop(text, regenerate):
        return None

    live, clock, said, _, llm, state = _setup(lines=["家に入れたよ"])
    live._guard = _Guard(drop)
    live._mark(state["activity"])
    live._last_intent = "北の森で原木を集めてくるね"
    state["activity"] = Activity(
        acting=True, goal=GOAL, intent="北の森で原木を集めてくるね", observation=_obs({"oak_log": 4})
    )
    clock.now = 20
    await live.tick()

    assert said == []
