"""ゲームのエージェントの Composition Root のテスト。"""

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("google.genai", reason="google-genai not installed")
pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from ailoveshen.application.use_cases.objectives import ObjectiveKeeper  # noqa: E402
from ailoveshen.domain.entities import Conversation  # noqa: E402
from ailoveshen.domain.value_objects import GoalPredicate  # noqa: E402
from ailoveshen.factories.game import create_game_service, create_objective_plan  # noqa: E402
from ailoveshen.infrastructure.config import (  # noqa: E402
    CharacterSettings,
    GeminiSettings,
    JevSettings,
    MinecraftSettings,
    MissionSettings,
)
from ailoveshen.presentation.services.game_service import GameService  # noqa: E402


def _create(gemini_key="g", jev_key="j"):
    return create_game_service(
        gemini=GeminiSettings(api_key=gemini_key),
        jev=JevSettings(api_key=jev_key),
        minecraft=MinecraftSettings(),
        character=CharacterSettings(),
        event_publisher=AsyncMock(),
        conversation=Conversation(),
    )


class TestCreateGameService:
    """create_game_service のテスト。"""

    @pytest.mark.asyncio
    async def test_creates_service(self):
        """正しい設定ならサービスが組み立てられる。"""
        service = _create()

        assert isinstance(service, GameService)
        assert isinstance(service.objectives, ObjectiveKeeper)
        await service.close()

    def test_missing_gemini_key_raises(self):
        """Gemini のキーがなければすぐ失敗する。"""
        with pytest.raises(ValueError, match="Gemini API key"):
            _create(gemini_key="")

    @pytest.mark.asyncio
    async def test_tools_control_is_wired(self, tmp_path):
        """control: tools なら、道具のステップと見張りが組み立てられる。"""
        minecraft = MinecraftSettings(control="tools")
        minecraft.watch.record_dir = str(tmp_path)
        service = create_game_service(
            gemini=GeminiSettings(api_key="g"),
            jev=JevSettings(api_key="j"),
            minecraft=minecraft,
            character=CharacterSettings(),
            event_publisher=AsyncMock(),
            conversation=Conversation(),
        )
        assert service._advance._control == "tools"
        assert service._advance._watcher is not None
        await service.close()

    def test_missing_jev_key_raises(self):
        """TypeSafe のキーがなければすぐ失敗する。"""
        with pytest.raises(ValueError, match="TypeSafe API key"):
            _create(jev_key="")


class TestCreateObjectivePlan:
    """最終目標の設定から作る計画のテスト。"""

    def test_default_mission_and_objectives(self):
        """設定した大目標が、順番どおり、上限つきでリストになる。"""
        plan = create_objective_plan(
            MissionSettings(
                viewer_budget_steps=50,
                objectives=[
                    {"title": "身を守る道具を持つ", "conditions": [{"predicate": "have", "item": "wooden_sword", "count": 1}]}
                ],
            )
        )

        assert plan.mission.text == "生き延びながら家を建て、街にしていく"
        assert [g.describe() for g in plan.pending] == ["身を守る道具を持つ (have(wooden_sword, 1))"]
        assert plan.viewer_budget == 50
        assert plan.playbook == ()

    def test_the_playbook_keeps_the_order_of_the_settings(self):
        """定石は、設定の順にそのまま段になる（大目標にするのは今の段だけ。docs/design/74）。"""
        plan = create_objective_plan(
            MissionSettings(
                playbook=[
                    {"name": "bed", "title": "寝床", "conditions": [{"predicate": "placed", "item": "bed"}]},
                    {
                        "name": "food",
                        "title": "畑",
                        "conditions": [
                            {"predicate": "facility", "name": "wheat_farm_1"},
                            {"predicate": "have", "item": "wheat", "count": 3},
                        ],
                        "reason": "食料",
                    },
                ]
            )
        )
        assert [s.name for s in plan.playbook] == ["bed", "food"]
        assert [c.describe() for c in plan.playbook[1].conditions] == ["facility(wheat_farm_1)", "have(wheat, 3)"]
        assert plan.pending == ()

    def test_condition_not_judged_from_the_world_is_rejected(self):
        """ブリッジが判定できない大目標は、開始時に失敗する。"""
        settings = MissionSettings(
            objectives=[{"title": "探検", "conditions": [{"predicate": "explored", "distance": 30}]}]
        )
        with pytest.raises(ValueError, match="explored cannot be a condition"):
            create_objective_plan(settings)

    def test_conditions_are_parsed(self):
        """placed は拠点に置くことを表す。"""
        plan = create_objective_plan(
            MissionSettings(
                objectives=[
                    {"title": "寝る", "conditions": [{"predicate": "placed", "item": "bed"}]}
                ]
            )
        )
        (condition,) = plan.current.conditions
        assert (condition.predicate, condition.where) == (GoalPredicate.PLACED, "home")
