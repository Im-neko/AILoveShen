"""アバターのファクトリー（Composition Root）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ailoveshen.application.use_cases.avatar_director import AvatarDirector
from ailoveshen.domain.value_objects import Activity
from ailoveshen.infrastructure.config import AvatarSettings, JevSettings
from ailoveshen.presentation.web.avatar import AvatarStage


def create_avatar_stage(
    avatar: AvatarSettings,
    jev: JevSettings,
    activity: Callable[[], Activity | None],
    base_dir: Path | None = None,
) -> AvatarStage:
    """
    アバターの合図を作るものを組み立てる（docs/design/24_avatar.md）。

    `avatar.judge` が "jev" で TypeSafe のキーがあれば、表情としぐさを Jev が選ぶ（規則は
    フォールバック）。キーがなければ規則だけ。閉じるときは `AvatarStage.close()`。

    Args:
        avatar: アバターの設定（settings.avatar）
        jev: Jev の設定（settings.jev）
        activity: 配信者が今していること（ゲームのセッションの activity）
        base_dir: model_path と record_dir の基準（既定はカレントディレクトリ）

    Raises:
        ValueError: avatar.judge や lip_sync が不明なとき
    """
    if avatar.judge not in ("jev", "rules"):
        raise ValueError(f"avatar.judge must be 'jev' or 'rules', got {avatar.judge!r}")
    base = base_dir or Path.cwd()
    director = None
    if avatar.judge == "jev" and jev.api_key:
        from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge
        from ailoveshen.infrastructure.adapters.storage.jsonl_watch_recorder import (
            JsonlWatchRecorder,
        )

        director = AvatarDirector(
            judge=JevFastJudge(
                api_key=jev.api_key, model=jev.model, timeout_seconds=jev.timeout_seconds
            ),
            activity=activity,
            timeout_seconds=avatar.judge_timeout_seconds,
            recorder=JsonlWatchRecorder(str(base / avatar.record_dir))
            if avatar.record_dir
            else None,
        )
    return AvatarStage(
        model_path=base / avatar.model_path,
        lip_sync=avatar.lip_sync,
        director=director,
    )
