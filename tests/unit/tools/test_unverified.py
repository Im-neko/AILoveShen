"""tools/unverified.py（WORKLOG から未確認の一覧を作る、設計書 90）のテスト。"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "unverified", Path(__file__).resolve().parents[3] / "tools" / "unverified.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)

WORKLOG = """\
## Completed Work

### 全体が未確認（設計書 1、2026-10-01、未確認、user: 「見て」）
- 作った
- 実機で見ること: 夜に出るか

### 確かめたが残りがある（2026-10-02）
- 実機未確認: 海からの上陸
- ほかの行

### 確認済み（設計書 2、2026-10-03、確認済み 2026-10-04）
- 実機未確認: これは数えない

### 日付のない見出し
- 未確認 は数えない

### 何も残っていない（2026-10-05）
- 全部確かめた
"""


def test_whole_and_leftover_entries_are_listed_and_the_rest_is_not():
    entries = tool.parse(WORKLOG)

    assert [(e.day, e.whole) for e in entries] == [(date(2026, 10, 1), True), (date(2026, 10, 2), False)]
    assert entries[1].lines == ("- 実機未確認: 海からの上陸",)


def test_an_entry_with_the_done_mark_in_its_heading_is_dropped_even_if_the_body_says_so():
    titles = [e.title for e in tool.parse(WORKLOG)]
    assert not any("確認済み" in t for t in titles)


def test_the_summary_counts_by_age_and_separates_whole_from_leftover():
    text = tool.render(tool.parse(WORKLOG), date(2026, 10, 7), brief=True)

    assert "2 件（全体が未確認 1、本文に残りがある 1）" in text
    assert "最も古い 2026-10-01（6 日前）" in text
    assert "7 日以内 2" in text


def test_main_reads_the_real_worklog_format(tmp_path, capsys):
    f = tmp_path / "WORKLOG.md"
    f.write_text(WORKLOG, encoding="utf-8")

    assert tool.main(["--worklog", str(f), "--today", "2026-10-07", "--since", "2026-10-02"]) == 0

    out = capsys.readouterr().out
    assert "1 件" in out and "海からの上陸" in out


def test_nothing_unverified_says_so():
    assert tool.render([], date(2026, 10, 7), brief=False) == "未確認の記録はない"
