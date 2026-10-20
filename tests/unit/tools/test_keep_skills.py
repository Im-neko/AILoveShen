"""世界の作り直しで、よく使われて成功率の高い技だけを残す道具のテスト（設計書 201）。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

import keep_skills as tool  # noqa: E402


def skill(*versions):
    return {"name": "s", "versions": [{"uses": u, "successes": s} for u, s in versions]}


def test_only_a_current_version_used_100_times_with_over_95_percent_is_kept():
    assert tool.keeps(skill((100, 100)))
    assert tool.keeps(skill((200, 191)))  # 95.5%
    assert not tool.keeps(skill((200, 190)))  # ちょうど 95% は残さない
    assert not tool.keeps(skill((99, 99)))  # 使われた回数が足りない
    assert not tool.keeps(skill((0, 0)))
    assert not tool.keeps({"versions": []})


def test_the_current_version_decides_not_the_older_ones():
    assert tool.keeps(skill((3, 0), (3, 0), (100, 100)))  # 古い版の失敗は数えない
    assert not tool.keeps(skill((100, 100), (3, 0)))  # 新しい版が試されたばかり


def test_kept_skills_are_copied_with_their_record_and_the_rest_stay_behind(tmp_path, monkeypatch):
    src, dst = tmp_path / "skills-old", tmp_path / "skills"
    src.mkdir()
    (src / "good.json").write_text(json.dumps(skill((120, 118))), encoding="utf-8")
    (src / "bad.json").write_text(json.dumps(skill((9, 0))), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--from", str(src), "--to", str(dst)])
    assert tool.main() == 0
    assert [p.name for p in dst.iterdir()] == ["good.json"]
    assert json.loads((dst / "good.json").read_text("utf-8"))["versions"][0]["uses"] == 120
    assert (src / "bad.json").exists()  # 取り分けたほうは変えない
