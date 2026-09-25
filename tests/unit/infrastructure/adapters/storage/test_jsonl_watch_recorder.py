"""JsonlWatchRecorder のテスト。"""

import json

from ailoveshen.infrastructure.adapters.storage.jsonl_watch_recorder import JsonlWatchRecorder


def test_each_tick_is_one_line(tmp_path):
    recorder = JsonlWatchRecorder(str(tmp_path / "watch"))
    recorder.record({"tool": "dig(x=1)", "answers": {"watch_0": {"value": True}}})
    recorder.record({"tool": "dig(x=1)", "fired": "woke up: q"})
    lines = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    assert [line["tool"] for line in lines] == ["dig(x=1)", "dig(x=1)"]
    assert lines[1]["fired"] == "woke up: q"
    assert "at" in lines[0]


def test_a_write_failure_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    JsonlWatchRecorder(str(blocker / "sub")).record({"x": 1})  # ディレクトリにできない
