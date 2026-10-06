"""tools/prompt_budget.py（記録から、プロンプトの大きさと内訳を見る、設計書 90）のテスト。"""

import importlib.util
import json
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "prompt_budget", Path(__file__).resolve().parents[3] / "tools" / "prompt_budget.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)

PROMPT = "前置き\n## 今していること\n" + "あ" * 100 + "\n## 直近の道具（古い順）\n" + "い" * 40


def test_sections_split_on_headings_and_keep_the_preamble():
    got = tool.sections(PROMPT)
    assert set(got) == {tool.PREAMBLE, "今していること", "直近の道具"}
    assert got["今していること"] > got["直近の道具"] > got[tool.PREAMBLE]


def test_the_same_heading_is_added_up():
    got = tool.sections("## A\n" + "あ" * 10 + "\n## A\n" + "あ" * 10)
    assert list(got) == ["A"]
    assert got["A"] >= 20


def test_summary_counts_the_real_tokens_time_and_tools_per_purpose():
    entries = [
        {"purpose": "tool", "prompt": PROMPT, "tokens": {"prompt": 7000}, "elapsed_ms": 4000, "tools": ["a"] * 38},
        {"purpose": "tool", "prompt": PROMPT, "tokens": {"prompt": 9000}, "elapsed_ms": 6000, "tools": ["a"] * 38},
        {"purpose": "reply", "prompt": "x", "tokens": {"prompt": 10}},
        {"purpose": "tool", "prompt": PROMPT, "error": "timeout"},  # 失敗は数えない
    ]
    rows = tool.summarize(entries, {"tool"})
    assert set(rows) == {"tool"}
    assert rows["tool"]["real"] == [7000, 9000]
    text = tool.format_summary(rows)
    assert "tool: 2 件" in text
    assert "中央値 8000" in text
    assert "38 個の定義" in text


def test_main_reads_files_and_filters_by_time(tmp_path, capsys):
    f = tmp_path / "gen-1.jsonl"
    rows = [
        {"at": "2026-10-06T00:00:00+00:00", "purpose": "tool", "prompt": PROMPT, "tokens": {"prompt": 1}},
        {"at": "2026-10-08T00:00:00+00:00", "purpose": "tool", "prompt": PROMPT, "tokens": {"prompt": 2}},
    ]
    f.write_text("".join(json.dumps(r) + "\n" for r in rows) + "{broken", encoding="utf-8")

    assert tool.main([str(f), "--since", "2026-10-07 00:00"]) == 0
    assert "tool: 1 件" in capsys.readouterr().out
