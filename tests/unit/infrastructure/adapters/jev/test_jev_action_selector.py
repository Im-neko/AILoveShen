"""Tests for JevActionSelector adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("typesafe_sdk", reason="typesafe-sdk not installed")

from typesafe_sdk import Choice, TypeSafeAPIConnectionError  # noqa: E402

from ailoveshen.domain.exceptions import ActionSelectionError  # noqa: E402
from ailoveshen.domain.value_objects import AvailableAction  # noqa: E402
from ailoveshen.infrastructure.adapters.jev.jev_action_selector import (  # noqa: E402
    JevActionSelector,
)

MODULE = "ailoveshen.infrastructure.adapters.jev.jev_action_selector"
ACTIONS = (AvailableAction("collect_log", "Chop a log"), AvailableAction("explore", "Walk"))


@pytest.fixture
def mock_client():
    """Patch AsyncTypeSafeClient and return the mock class."""
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
    """Tests for select() and construction."""

    def test_empty_api_key_raises(self, mock_client):
        """Test a missing API key raises ValueError."""
        with pytest.raises(ValueError, match="API key is required"):
            JevActionSelector(api_key="")

    @pytest.mark.asyncio
    async def test_sends_one_choice_question(self, mock_client):
        """Test the candidates become the criteria of one Choice question."""
        selector = JevActionSelector(api_key="k", model="jev-latest")

        await selector.select({"hp": 20}, ACTIONS, "pick one")

        kwargs = mock_client.return_value.system_one.call_args.kwargs
        assert kwargs["state"] == {"hp": 20}
        assert kwargs["model"] == "jev-latest"
        question = kwargs["questions"]["action"]
        assert isinstance(question, Choice)
        assert question.instructions == "pick one"
        assert dict(question.criteria) == {"collect_log": "Chop a log", "explore": "Walk"}

    @pytest.mark.asyncio
    async def test_returns_decision(self, mock_client):
        """Test the answer becomes an ActionDecision."""
        decision = await JevActionSelector(api_key="k").select({}, ACTIONS, "pick")

        assert decision.action_id == "collect_log"
        assert decision.confidence == 0.9
        assert decision.probabilities == {"collect_log": 0.95}

    @pytest.mark.asyncio
    async def test_sdk_error_raises_selection_error(self, mock_client):
        """Test SDK errors become ActionSelectionError."""
        mock_client.return_value.system_one.side_effect = TypeSafeAPIConnectionError("down")

        with pytest.raises(ActionSelectionError, match="Jev request failed"):
            await JevActionSelector(api_key="k").select({}, ACTIONS, "pick")

    @pytest.mark.asyncio
    async def test_close(self, mock_client):
        """Test close() closes the SDK client."""
        await JevActionSelector(api_key="k").close()

        mock_client.return_value.aclose.assert_awaited_once()
