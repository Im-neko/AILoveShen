"""アバターの Composition Root のテスト。"""

import pytest

pytest.importorskip("fastapi", reason="ailoveshen[stream] not installed")
pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from ailoveshen.factories.avatar import create_avatar_stage  # noqa: E402
from ailoveshen.infrastructure.config import AvatarSettings, JevSettings  # noqa: E402


def test_jev_decides_when_there_is_a_key(tmp_path):
    stage = create_avatar_stage(AvatarSettings(), JevSettings(api_key="k"), lambda: None, tmp_path)
    assert stage._director is not None


def test_rules_only_without_a_key_or_when_asked(tmp_path):
    no_key = create_avatar_stage(AvatarSettings(), JevSettings(), lambda: None, tmp_path)
    rules = create_avatar_stage(
        AvatarSettings(judge="rules"), JevSettings(api_key="k"), lambda: None, tmp_path
    )
    assert no_key._director is None and rules._director is None
    with pytest.raises(ValueError):
        create_avatar_stage(AvatarSettings(judge="gpt"), JevSettings(), lambda: None, tmp_path)
