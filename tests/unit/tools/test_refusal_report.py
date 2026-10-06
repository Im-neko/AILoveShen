"""tools/refusal_report.py（断られた手を理由の種類ごとに数える、設計書 90）のテスト。"""

import importlib.util
import json
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "refusal_report", Path(__file__).resolve().parents[3] / "tools" / "refusal_report.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def _row(result, tool_name="dig", refused=True, event="result", at="2026-10-06T00:00:00+00:00"):
    return {"event": event, "tool": tool_name, "refused": refused, "result": result, "at": at}


def test_reasons_are_told_apart_by_the_first_pattern_that_matches():
    cases = {
        "refused: the reflex is handling a nearby threat": "反射が対処中",
        "refused: staying inside for the night: actions outside the house wait": "夜の籠もり",
        "refused: the block at 1,2,3 is 40.2m away; did you drop a minus sign?": "距離の間違い・遠すぎ",
        "refused: there is air at 1,2,3, nothing to dig": "古い対象（もう空気・提案にない）",
        "refused: cannot craft torch x4: needs 1 coal": "材料・食料・道具がない",
        "refused: you have no cobblestone": "材料・食料・道具がない",
        "refused: stone drops nothing without one of: wooden_pickaxe": "材料・食料・道具がない",
        "refused: something nobody has seen": tool.OTHER,
    }
    for text, expected in cases.items():
        assert tool.reason_of(text) == expected, text


def test_only_result_events_that_were_refused_are_counted_as_refused():
    rows = [
        _row("refused: the reflex is handling a nearby threat"),
        _row("done", refused=False),
        _row("failed: no path", refused=False),
        {"event": "pick", "refused": True},  # result でない行は read_results が落とす
    ]
    results = [r for r in rows if r["event"] == "result"]

    total, refused = tool.summarize(results)

    assert total == 3
    assert refused == {"反射が対処中": 1}


def test_counting_by_tool():
    rows = [_row("refused: x", "dig"), _row("refused: y", "dig"), _row("refused: z", "goto")]
    _, refused = tool.summarize(rows, by="tool")
    assert refused["dig"] == 2 and refused["goto"] == 1


def test_main_filters_by_time_and_ignores_broken_lines(tmp_path, capsys):
    f = tmp_path / "watch-1.jsonl"
    lines = [
        _row("refused: the reflex is handling", at="2026-10-05T00:00:00+00:00"),
        _row("refused: the reflex is handling", at="2026-10-07T00:00:00+00:00"),
        _row("done", refused=False, at="2026-10-07T00:00:00+00:00"),
    ]
    f.write_text("".join(json.dumps(r) + "\n" for r in lines) + "{broken", encoding="utf-8")

    assert tool.main([str(f), "--since", "2026-10-06 00:00"]) == 0

    out = capsys.readouterr().out
    assert "結果 2 件のうち断られた手 1 件（50.0%）" in out
    assert "反射が対処中" in out


def test_no_results_says_so():
    assert tool.render(0, tool.Counter()) == "結果の記録がない"
