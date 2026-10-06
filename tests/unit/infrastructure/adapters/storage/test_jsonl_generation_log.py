"""JsonlGenerationLog（LLM の呼び出しの記録をファイルにも残す）のテスト。"""

import json
import os
import time

import pytest

from ailoveshen.infrastructure.adapters.storage import JsonlGenerationLog


def _lines(log: JsonlGenerationLog) -> list[dict]:
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]


def test_record_appends_a_line_and_keeps_the_memory_view(tmp_path):
    log = JsonlGenerationLog(str(tmp_path / "gen"), size=5)
    log.record({"purpose": "tool", "prompt": "全文", "output": None, "model": "local:x"})
    log.record({"purpose": "goal", "prompt": "p2", "output": "{}", "model": "local:x"})

    rows = _lines(log)
    assert [r["purpose"] for r in rows] == ["tool", "goal"]
    assert rows[0]["prompt"] == "全文"
    assert [e["purpose"] for e in log.recent()] == ["goal", "tool"]


def test_images_stay_in_memory_only(tmp_path):
    log = JsonlGenerationLog(str(tmp_path))
    image_id = log.record_image(b"\x89PNG", "image/png")
    log.record({"purpose": "screen_review", "images": [{"id": image_id, "bytes": 4}]})

    assert log.image(image_id) == (b"\x89PNG", "image/png")
    assert b"PNG" not in log.path.read_bytes()


def test_unwritable_directory_does_not_stop_generation(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    log = JsonlGenerationLog(str(blocker / "sub"))

    log.record({"purpose": "tool"})  # 例外を投げない

    assert log.recent()[0]["purpose"] == "tool"


def test_old_files_are_pruned_at_start_and_new_ones_kept(tmp_path):
    old = tmp_path / "gen-20200101T000000Z.jsonl"
    fresh = tmp_path / "gen-20991231T000000Z.jsonl"
    other = tmp_path / "notes.txt"
    for f in (old, fresh, other):
        f.write_text("x")
    long_ago = time.time() - 10 * 86400
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))

    JsonlGenerationLog(str(tmp_path), keep_days=3)

    assert not old.exists()
    assert fresh.exists()
    assert other.exists()  # gen-*.jsonl 以外は触らない


def test_keep_days_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        JsonlGenerationLog(str(tmp_path), keep_days=0)
