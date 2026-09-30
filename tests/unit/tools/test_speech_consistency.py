"""tools/speech_consistency.py（発言と行動の一貫性の測定、設計書 65 §2）のテスト。"""

import importlib.util
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "speech_consistency", Path(__file__).resolve().parents[3] / "tools" / "speech_consistency.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool  # dataclass が自分のモジュールを引く
SPEC.loader.exec_module(tool)

NEW = "新しい小目標（これからやること。まだ済んでいない）: "


def _row(at: str, events: list[str], speak: bool = True) -> dict:
    return {"at": f"2026-09-30T07:{at}+00:00", "events": events, "speak": speak}


def _step(at: str, event: str, **kw) -> dict:
    return {"at": f"2026-09-30T07:{at}+00:00", "event": event, **kw}


class TestForecasts:
    def test_a_goal_that_met_after_acting_and_one_that_was_dropped(self):
        commentary = [
            _row("00:00", [NEW + "have(log, 4)（「家」のため。木が要る）。今いる場所: 家の外（家まで 8m）"]),
            _row("01:00", ["小目標 have(log, 4) が達成（goal have(log, 4) is met）", NEW + "have(torch, 4)（身を守るため。暗い）"]),
            _row("02:30", ["小目標 have(torch, 4) が未達成でやめた（the time of day changed from day to dusk）"]),
        ]
        steps = [
            _step("00:00", "pick", goal="have(log, 4)", step=1),
            _step("00:10", "result", ok=True),
            _step("01:01", "pick", goal="have(torch, 4)", step=1),
            _step("01:05", "result", ok=False),
        ]

        log, torch = tool.forecasts_of(commentary, steps)

        assert (log.goal, log.met, log.steps, log.ok_steps) == ("have(log, 4)", True, 1, 1)
        assert log.first_ok_after == 10
        assert (torch.goal, torch.abandoned, torch.steps, torch.ok_steps) == (
            "have(torch, 4)",
            True,
            1,
            0,
        )
        assert torch.life_seconds == 90

    def test_a_skipped_row_carries_its_events_over_and_counts_once(self):
        commentary = [
            _row("00:00", [NEW + "have(log, 4)（a）"], speak=False),
            _row("00:30", [NEW + "have(log, 4)（a）", "一緒に追っている…"], speak=True),
            _row("01:00", ["小目標 have(log, 4) が達成（goal met）"]),
        ]

        (log,) = tool.forecasts_of(commentary, [])

        assert log.spoken  # 持ち越した行で話した
        assert log.said_at.second == 0
        assert log.steps == 0

    def test_the_report_counts_what_was_abandoned_before_any_action(self):
        commentary = [
            _row("00:00", [NEW + "at_home()（身を守るため）"]),
            _row("00:20", ["小目標 at_home() が未達成でやめた（goal at_home() is stuck (actions keep failing)）"]),
        ]

        text = tool.report_forecasts(tool.forecasts_of(commentary, []))

        assert "話した新しい小目標: 1 件" in text
        assert "未達成でやめた: 1 (100%)" in text
        assert "行動が 1 つもないまま終わった: 1 (100%)" in text
        assert "stuck 1" in text


class TestLines:
    def _rows(self) -> list[dict]:
        state = {"current_goal": "have(torch, 4)", "current_action": "松明を作る", "inside_home": False, "home_distance_m": 16}
        return [
            {"at": "2026-09-30T07:00:00+00:00", "event": "line", "text": "家に入ったよ", "source": "commentary", "outcome": "said", "state": state},
            {"at": "2026-09-30T07:00:30+00:00", "event": "line", "text": "松明を作るね", "source": "commentary", "outcome": "said", "state": state},
            {"at": "2026-09-30T07:01:00+00:00", "event": "line", "text": "考えるね", "source": "notice", "outcome": "said", "state": state},
            {"at": "2026-09-30T07:00:01+00:00", "event": "tts", "text": "家に入ったよ", "source": "commentary", "outcome": "played", "waited": 0.5},
            {"at": "2026-09-30T07:00:31+00:00", "event": "tts", "text": "松明を作るね", "source": "commentary", "outcome": "dropped_old", "waited": 25.0},
        ]

    def test_only_narration_lines_are_kept_and_unheard_ones_are_marked(self):
        lines = tool.lines_of(self._rows())

        assert [(l.text, l.heard) for l in lines] == [("家に入ったよ", True), ("松明を作るね", False)]

    def test_jev_answers_are_compared_with_the_state_like_the_stream_does(self):
        (heard, _) = tool.lines_of(self._rows())

        tool.classify(heard, {"claims_inside_home": [True, 0.9], "contradicts_plan": [True, 0.5]})

        assert heard.type2 and not heard.type1  # 家の外なのに家の中と言った。低い確信度の食い違いは数えない
        assert "家の外（家まで 16m）" in heard.problems[0]

    def test_a_contradiction_with_the_plan_is_type_one(self):
        (line, _) = tool.lines_of(self._rows())

        tool.classify(line, {"contradicts_plan": [True, 0.9]})

        assert line.type1 and not line.type2


class TestVerdicts:
    def test_rewritten_and_dropped_lines_are_counted_from_the_check_rows(self):
        """作り直した文（元の文の check）と言わなかった文は `line` の記録がないが、数に入る。"""
        rows = [
            {"at": "2026-09-30T07:00:00+00:00", "event": "check", "text": "家に入れたよ", "verdict": "rewrite", "attempt": 1},
            {"at": "2026-09-30T07:00:01+00:00", "event": "check", "text": "まだ外だよ", "verdict": "ok", "attempt": 2},
            {"at": "2026-09-30T07:01:00+00:00", "event": "check", "text": "松明ができたよ", "verdict": "rewrite", "attempt": 1},
            {"at": "2026-09-30T07:01:01+00:00", "event": "check", "text": "松明ができたよ！", "verdict": "dropped", "attempt": 2},
            {"at": "2026-09-30T07:02:00+00:00", "event": "check", "text": "静かだね", "verdict": "unchecked", "attempt": 1},
        ]

        verdicts = tool.verdicts_of(rows)
        text = tool.report_lines(tool.lines_of(rows), judged=False, verdicts=verdicts)

        assert verdicts == {"rewrite": 2, "ok": 1, "dropped": 1, "unchecked": 1}
        assert "配信中の照合: rewrite 2、ok 1、dropped 1、unchecked 1" in text
