"""JevActionSelector アダプタのテスト。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from typesafe_sdk import Choice, TypeSafeAPIConnectionError  # noqa: E402

from ailoveshen.domain.exceptions import ActionSelectionError  # noqa: E402
from ailoveshen.domain.value_objects import Candidate  # noqa: E402
from ailoveshen.infrastructure.adapters.jev.jev_action_selector import (  # noqa: E402
    JevActionSelector,
)

MODULE = "ailoveshen.infrastructure.adapters.jev.jev_action_selector"
ACTIONS = (
    Candidate("collect_log", {"verb": "dig", "target": "oak_log", "distance": 3}),
    Candidate("explore", {"verb": "explore", "target": "north"}),
)


@pytest.fixture
def mock_client():
    """AsyncTypeSafeClient を差し替えて、モックのクラスを返す。"""
    with patch(f"{MODULE}.AsyncTypeSafeClient") as client_cls:
        client = MagicMock()
        answer = MagicMock(
            choice="collect_log", confidence=0.9, probabilities={"collect_log": 0.95}
        )
        response = MagicMock(model="jev-1.13.0", choices={"action": answer})
        response.usage.input_tokens = 100
        client.system_one = AsyncMock(return_value=response)
        client.aclose = AsyncMock()
        client_cls.return_value = client
        yield client_cls


class TestJevActionSelector:
    """select() と生成のテスト。"""

    def test_empty_api_key_raises(self, mock_client):
        """API キーがなければ ValueError。"""
        with pytest.raises(ValueError, match="API key is required"):
            JevActionSelector(api_key="")

    @pytest.mark.asyncio
    async def test_sends_one_choice_question(self, mock_client):
        """候補（JSON の説明）が、1 つの Choice の質問の criteria になる。"""
        selector = JevActionSelector(api_key="k", model="jev-latest")

        await selector.select({"hp": 20}, ACTIONS, "pick one")

        kwargs = mock_client.return_value.system_one.call_args.kwargs
        assert kwargs["state"] == {"hp": 20}
        assert kwargs["model"] == "jev-latest"
        question = kwargs["questions"]["action"]
        assert isinstance(question, Choice)
        assert question.instructions == "pick one"
        assert dict(question.criteria) == {
            "collect_log": {"verb": "dig", "target": "oak_log", "distance": 3},
            "explore": {"verb": "explore", "target": "north"},
        }

    @pytest.mark.asyncio
    async def test_returns_decision(self, mock_client):
        """答えが ActionDecision になる。"""
        decision = await JevActionSelector(api_key="k").select({}, ACTIONS, "pick")

        assert decision.action_id == "collect_log"
        assert decision.confidence == 0.9
        assert decision.probabilities == {"collect_log": 0.95}

    @pytest.mark.asyncio
    async def test_sdk_error_raises_selection_error(self, mock_client):
        """SDK のエラーは ActionSelectionError になる。"""
        mock_client.return_value.system_one.side_effect = TypeSafeAPIConnectionError("down")

        with pytest.raises(ActionSelectionError, match="Jev request failed"):
            await JevActionSelector(api_key="k").select({}, ACTIONS, "pick")

    @pytest.mark.asyncio
    async def test_close(self, mock_client):
        """close() は SDK のクライアントを閉じる。"""
        await JevActionSelector(api_key="k").close()

        mock_client.return_value.aclose.assert_awaited_once()
