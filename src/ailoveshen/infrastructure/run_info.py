"""実行（プレイの始まり）の条件を集める: どのコードと設定とモデルの結果かを後で比べるため。"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from ailoveshen.infrastructure.config import (
    JevSettings,
    LlmSettings,
    MinecraftSettings,
)

REPO_DIR = Path(__file__).resolve().parents[3]
GIT_TIMEOUT_SECONDS = 5.0


def _git(repo_dir: Path, *args: str) -> str | None:
    """git の出力。git がない、リポジトリでない、時間切れなら None。"""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_state(repo_dir: Path = REPO_DIR) -> dict[str, Any]:
    """
    コードの版: commit、ブランチ、未コミットの変更の有無と、その変更の指紋。

    未コミットの変更が常にある開発では commit だけでは版が区別できないので、`git diff HEAD`
    の SHA-1 の先頭を添える（同じ変更なら同じ値）。追跡していない新しいファイルは数えない。
    """
    commit = _git(repo_dir, "rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "branch": None, "dirty": None, "diff": None}
    branch = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git(repo_dir, "status", "--porcelain")
    diff = _git(repo_dir, "diff", "HEAD")
    return {
        "commit": commit.strip(),
        "branch": branch.strip() if branch else None,
        "dirty": bool(status.strip()) if status is not None else None,
        "diff": hashlib.sha1(diff.encode("utf-8")).hexdigest()[:12] if diff else None,
    }


def collect_run_info(
    jev: JevSettings,
    minecraft: MinecraftSettings,
    llm: LlmSettings | None = None,
    repo_dir: Path = REPO_DIR,
) -> dict[str, Any]:
    """実行ごとの条件の 1 件（成績を比べるときの軸）。"""
    local = llm.local if llm is not None else None
    return {
        "event": "run",
        "git": git_state(repo_dir),
        "app_env": os.environ.get("APP_ENV", "development"),
        "control": minecraft.control,
        "small_goals": minecraft.small_goals,
        "step_picker": minecraft.step_picker,
        "failure_routing": minecraft.failure_routing,
        "remote_llm": {"model": llm.litellm.model, "models": dict(llm.litellm.models)} if llm is not None else None,
        "jev_model": jev.model,
        "local_llm": (
            {
                "model": local.model,
                "routes": dict(llm.routes) if llm is not None else {},
                "fallback": llm.fallback if llm is not None else None,
            }
            if local is not None and local.enabled
            else None
        ),
        "playbook": [str(step.get("name")) for step in minecraft.mission.playbook],
    }
