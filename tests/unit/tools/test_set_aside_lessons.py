"""世界の作り直しで、一度も効かなかった失敗由来の教訓を取り分ける道具のテスト（設計書 201）。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

import set_aside_lessons as tool  # noqa: E402


def lesson(id, source="failure", helped=0, used=0):
    return {"id": id, "text": id, "source": source, "helped": helped, "used": used}


def test_only_failure_lessons_that_were_tried_and_never_helped_are_set_aside():
    lessons = [
        lesson("L1", used=3),
        lesson("L2", helped=1, used=3),
        lesson("L3", source="viewer:rin"),
        lesson("L4", source="viewer:運営"),
        lesson("L5", used=4),
        lesson("L6"),
    ]
    kept, aside = tool.split(lessons)
    assert [n["id"] for n in kept] == ["L2", "L3", "L4", "L6"]  # L6 はまだ思い出していない
    assert [n["id"] for n in aside] == ["L1", "L5"]


def test_the_tool_writes_both_files_and_keeps_the_counter(tmp_path, monkeypatch, capsys):
    path, out = tmp_path / "lessons.json", tmp_path / "unproven.json"
    path.write_text(json.dumps({"lessons": [lesson("L1", used=2), lesson("L2", helped=2, used=2)], "next_id": 9}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--lessons", str(path), "--out", str(out)])
    assert tool.main() == 0
    assert [n["id"] for n in json.loads(path.read_text("utf-8"))["lessons"]] == ["L2"]
    assert json.loads(path.read_text("utf-8"))["next_id"] == 9
    assert [n["id"] for n in json.loads(out.read_text("utf-8"))["lessons"]] == ["L1"]


def test_dry_run_changes_nothing_and_an_existing_output_is_refused(tmp_path, monkeypatch):
    path, out = tmp_path / "lessons.json", tmp_path / "unproven.json"
    body = json.dumps({"lessons": [lesson("L1", used=2)], "next_id": 2})
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--lessons", str(path), "--out", str(out), "--dry-run"])
    assert tool.main() == 0 and path.read_text("utf-8") == body and not out.exists()
    out.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--lessons", str(path), "--out", str(out)])
    assert tool.main() == 1 and path.read_text("utf-8") == body
