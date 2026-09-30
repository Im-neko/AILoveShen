"""SpokenLog（読み上げの記録、設計書 65 §2）のテスト。"""

from ailoveshen.application.dto.speech_dto import SpeakTextRequest
from ailoveshen.application.ports.output.watch_recorder import IWatchRecorder
from ailoveshen.application.use_cases.speech_check import SpeechFinding
from ailoveshen.domain.value_objects import Activity, GameObservation, Goal, GoalPredicate, GoalSpec
from ailoveshen.presentation.services.spoken_log import SpokenLog


class _Recorder(IWatchRecorder):
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, entry: dict) -> None:
        self.entries.append(entry)


def _activity() -> Activity:
    obs = GameObservation(
        state={"home": {"door": {"x": 16, "z": 0}}, "self": {"position": {"x": 0, "z": 0}}},
        candidates=(),
        health=12.0,
        food=15,
        has_home=True,
        inside_home=False,
        time_phase="dusk",
    )
    return Activity(
        goal=Goal(spec=GoalSpec(GoalPredicate.HAVE, item="torch", count=4), reason="暗いから"),
        observation=obs,
        intent="松明を作る",
        acting=True,
    )


def test_a_line_carries_the_state_it_was_said_in():
    recorder = _Recorder()
    log = SpokenLog(recorder, lambda: _activity())

    log.line("松明を作るよ", "commentary")

    (entry,) = recorder.entries
    assert entry["event"] == "line"
    assert entry["text"] == "松明を作るよ"
    assert entry["source"] == "commentary"
    assert entry["outcome"] == "said"
    assert entry["state"]["current_goal"] == "have(torch, 4)"
    assert entry["state"]["current_action"] == "松明を作る"
    assert entry["state"]["inside_home"] is False
    assert entry["state"]["home_distance_m"] == 16
    assert entry["state"]["time_phase"] == "dusk"


def test_check_and_tts_lines_are_kept_apart():
    recorder = _Recorder()
    log = SpokenLog(recorder, lambda: None)

    log.check(
        "家に入ったよ",
        SpeechFinding(problems=("家の外",), checked=True, answers={"claims_inside_home": [True, 0.9]}),
        "rewrite",
        1,
    )
    log.tts(SpeakTextRequest(text="家に入ったよ", source="commentary"), "dropped_old", 31.04)

    assert [e["event"] for e in recorder.entries] == ["check", "tts"]
    assert recorder.entries[0]["verdict"] == "rewrite"
    assert recorder.entries[0]["problems"] == ["家の外"]
    assert recorder.entries[1]["outcome"] == "dropped_old"
    assert recorder.entries[1]["waited"] == 31.0
