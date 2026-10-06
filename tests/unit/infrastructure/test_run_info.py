"""実行の条件の収集（docs/design/82）のテスト。"""

import subprocess
from pathlib import Path

from ailoveshen.infrastructure.config import (
    GeminiSettings,
    JevSettings,
    LlmSettings,
    LocalLlmSettings,
    MinecraftSettings,
    MissionSettings,
)
from ailoveshen.infrastructure.run_info import collect_run_info, git_state


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


class TestGitState:
    """コードの版を、commit と未コミットの変更の指紋で区別する。"""

    def test_a_clean_repository_has_no_diff(self, tmp_path):
        _git(tmp_path, "init", "-q")
        (tmp_path / "a.txt").write_text("a")
        _git(tmp_path, "add", "a.txt")
        _git(tmp_path, "commit", "-q", "-m", "first")

        state = git_state(tmp_path)

        assert len(state["commit"]) == 40
        assert state["dirty"] is False
        assert state["diff"] is None

    def test_the_same_uncommitted_change_gives_the_same_fingerprint(self, tmp_path):
        _git(tmp_path, "init", "-q")
        (tmp_path / "a.txt").write_text("a")
        _git(tmp_path, "add", "a.txt")
        _git(tmp_path, "commit", "-q", "-m", "first")
        (tmp_path / "a.txt").write_text("b")

        first = git_state(tmp_path)
        second = git_state(tmp_path)
        (tmp_path / "a.txt").write_text("c")
        third = git_state(tmp_path)

        assert first["dirty"] is True
        assert first["diff"] == second["diff"]
        assert first["diff"] != third["diff"]

    def test_outside_a_repository_everything_is_unknown(self, tmp_path):
        state = git_state(tmp_path)

        assert state == {"commit": None, "branch": None, "dirty": None, "diff": None}


class TestCollectRunInfo:
    """成績を比べる軸になる設定とモデル。"""

    def test_it_collects_the_settings_that_change_the_results(self, tmp_path):
        minecraft = MinecraftSettings(
            mission=MissionSettings(playbook=[{"name": "shelter"}, {"name": "bed"}])
        )
        info = collect_run_info(
            GeminiSettings(main_model="g-main"),
            JevSettings(model="jev-x"),
            minecraft,
            LlmSettings(local=LocalLlmSettings(enabled=True, model="qwen")),
            repo_dir=tmp_path,
        )

        assert info["event"] == "run"
        assert info["control"] == "tools"
        assert info["jev_model"] == "jev-x"
        assert info["gemini_model"] == "g-main"
        assert info["local_llm"]["model"] == "qwen"
        assert info["playbook"] == ["shelter", "bed"]

    def test_a_disabled_local_llm_is_not_listed(self, tmp_path):
        info = collect_run_info(
            GeminiSettings(), JevSettings(), MinecraftSettings(), LlmSettings(), repo_dir=tmp_path
        )

        assert info["local_llm"] is None
