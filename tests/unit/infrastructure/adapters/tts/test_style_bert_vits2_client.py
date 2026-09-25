"""StyleBertVits2Client アダプタのテスト。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# httpx がなければ、このモジュールのテストは全部飛ばす
httpx = pytest.importorskip("httpx", reason="httpx not installed")

from ailoveshen.domain.exceptions import SynthesisError
from ailoveshen.domain.value_objects import EmotionState, EmotionType
from ailoveshen.infrastructure.adapters.tts.style_bert_vits2_client import (
    StyleBertVits2Client,
)


class TestStyleBertVits2Client:
    """StyleBertVits2Client アダプタのテスト。"""

    @pytest.fixture
    def client(self):
        """StyleBertVits2Client を作る。"""
        return StyleBertVits2Client(
            host="localhost",
            port=5000,
            timeout_seconds=10.0,
            model_name="test_model",
        )

    def test_init_defaults(self):
        """既定値で初期化する。"""
        client = StyleBertVits2Client()
        assert client._base_url == "http://localhost:5000"
        assert client._timeout == 30.0
        assert client._model_name == "default"

    def test_init_custom_values(self, client):
        """値を指定して初期化する。"""
        assert client._base_url == "http://localhost:5000"
        assert client._timeout == 10.0
        assert client._model_name == "test_model"

    def test_is_connected_initially_false(self, client):
        """connect の前は is_connected が False を返す。"""
        assert client.is_connected() is False

    @pytest.mark.asyncio
    async def test_synthesize_without_connect_raises(self, client):
        """接続していなければ synthesize はエラー。"""
        with pytest.raises(SynthesisError, match="not connected"):
            await client.synthesize("Hello", EmotionState())

    @pytest.mark.asyncio
    async def test_connect_success(self, client):
        """接続できる。"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"test_model": {"style2id": {"Neutral": 0}}}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            await client.connect()

            assert client.is_connected() is True

    @pytest.mark.asyncio
    async def test_disconnect(self, client):
        """disconnect はクライアントを消す。"""
        # まず接続する
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await client.connect()

        # 次に切断する
        with patch.object(httpx.AsyncClient, "aclose", new_callable=AsyncMock) as mock_close:
            await client.disconnect()

        assert client.is_connected() is False

    @pytest.mark.asyncio
    async def test_synthesize_success(self, client):
        """音声を合成できる。"""
        # 接続済みのクライアントを用意する
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}
        mock_response.headers = {"content-type": "audio/wav"}
        mock_response.content = b"audio_data"

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await client.connect()

            # synthesize を呼ぶ
            result = await client.synthesize(
                text="Hello",
                emotion=EmotionState(EmotionType.HAPPY, 0.8),
                speaker_id=1,
                language="JP",
            )

            assert result == b"audio_data"
            # 正しい引数が渡ったか確かめる
            call_args = mock_get.call_args_list[-1]
            params = call_args.kwargs.get("params", {})
            assert params["text"] == "Hello"
            assert params["style"] == "Happy"
            assert params["speaker_id"] == 1
            assert params["language"] == "JP"

    @pytest.mark.asyncio
    async def test_synthesize_http_error(self, client):
        """合成は HTTP のエラーを扱う。"""
        # 接続済みのクライアントを用意する
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
            # 1 回目は connect の呼び出し
            mock_get.return_value = mock_connect_response
            await client.connect()

            # 2 回目は synthesize の呼び出しで、失敗する
            mock_get.return_value = mock_error_response

            with pytest.raises(SynthesisError, match="500"):
                await client.synthesize("Hello", EmotionState())

    @pytest.mark.asyncio
    async def test_get_available_styles_success(self, client):
        """使えるスタイルを取る。"""
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
        """接続していなければ、get_available_styles は既定を返す。"""
        styles = await client.get_available_styles()
        assert styles == ["Neutral"]

    @pytest.mark.asyncio
    async def test_get_available_styles_model_not_found(self, client):
        """応答にモデルがなければ、get_available_styles は既定を返す。"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"other_model": {}}

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await client.connect()

            styles = await client.get_available_styles()

            assert styles == ["Neutral"]
