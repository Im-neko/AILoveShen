"""Tests for StyleBertVits2Client adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests in this module if httpx is not installed
httpx = pytest.importorskip("httpx", reason="httpx not installed")

from ailoveshen.core.exceptions import SynthesisError
from ailoveshen.tts.infrastructure.adapters.tts.style_bert_vits2_client import (
    StyleBertVits2Client,
)


class TestStyleBertVits2Client:
    """Tests for StyleBertVits2Client adapter."""

    @pytest.fixture
    def client(self):
        """Create a StyleBertVits2Client."""
        return StyleBertVits2Client(
            host="localhost",
            port=5000,
            timeout_seconds=10.0,
            model_name="test_model",
        )

    def test_init_defaults(self):
        """Test initialization with defaults."""
        client = StyleBertVits2Client()
        assert client._base_url == "http://localhost:5000"
        assert client._timeout == 30.0
        assert client._model_name == "default"

    def test_init_custom_values(self, client):
        """Test initialization with custom values."""
        assert client._base_url == "http://localhost:5000"
        assert client._timeout == 10.0
        assert client._model_name == "test_model"

    def test_is_connected_initially_false(self, client):
        """Test is_connected returns False before connect."""
        assert client.is_connected() is False

    @pytest.mark.asyncio
    async def test_synthesize_without_connect_raises(self, client):
        """Test synthesize raises error when not connected."""
        with pytest.raises(SynthesisError, match="not connected"):
            await client.synthesize("Hello")

    @pytest.mark.asyncio
    async def test_connect_success(self, client):
        """Test successful connection."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"test_model": {"style2id": {"Neutral": 0}}}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            await client.connect()

            assert client.is_connected() is True

    @pytest.mark.asyncio
    async def test_disconnect(self, client):
        """Test disconnect clears client."""
        # First connect
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await client.connect()

        # Then disconnect
        with patch.object(
            httpx.AsyncClient, "aclose", new_callable=AsyncMock
        ) as mock_close:
            await client.disconnect()

        assert client.is_connected() is False

    @pytest.mark.asyncio
    async def test_synthesize_success(self, client):
        """Test successful synthesis."""
        # Setup connected client
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}
        mock_response.headers = {"content-type": "audio/wav"}
        mock_response.content = b"audio_data"

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await client.connect()

            # Call synthesize
            result = await client.synthesize(
                text="Hello",
                style="Happy",
                speaker_id=1,
                language="JP",
            )

            assert result == b"audio_data"
            # Verify correct parameters were passed
            call_args = mock_get.call_args_list[-1]
            params = call_args.kwargs.get("params", {})
            assert params["text"] == "Hello"
            assert params["style"] == "Happy"
            assert params["speaker_id"] == 1
            assert params["language"] == "JP"

    @pytest.mark.asyncio
    async def test_synthesize_http_error(self, client):
        """Test synthesis handles HTTP errors."""
        # Setup connected client
        mock_connect_response = MagicMock()
        mock_connect_response.raise_for_status = MagicMock()
        mock_connect_response.json.return_value = {}

        mock_error_response = MagicMock()
        mock_error_response.status_code = 500
        mock_error_response.text = "Server error"
        mock_error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_error_response
        )

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            # First call for connect
            mock_get.return_value = mock_connect_response
            await client.connect()

            # Second call for synthesize - should fail
            mock_get.return_value = mock_error_response

            with pytest.raises(SynthesisError, match="500"):
                await client.synthesize("Hello")

    @pytest.mark.asyncio
    async def test_get_available_styles_success(self, client):
        """Test getting available styles."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "test_model": {
                "style2id": {
                    "Neutral": 0,
                    "Happy": 1,
                    "Sad": 2,
                }
            }
        }

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await client.connect()

            styles = await client.get_available_styles()

            assert "Neutral" in styles
            assert "Happy" in styles
            assert "Sad" in styles

    @pytest.mark.asyncio
    async def test_get_available_styles_not_connected(self, client):
        """Test get_available_styles returns default when not connected."""
        styles = await client.get_available_styles()
        assert styles == ["Neutral"]

    @pytest.mark.asyncio
    async def test_get_available_styles_model_not_found(self, client):
        """Test get_available_styles returns default when model not in response."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"other_model": {}}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await client.connect()

            styles = await client.get_available_styles()

            assert styles == ["Neutral"]
