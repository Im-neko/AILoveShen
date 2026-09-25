"""Style-Bert-VITS2 への HTTP クライアントのアダプター。"""

from __future__ import annotations

from typing import List, Optional

import httpx
from loguru import logger

from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.domain.exceptions import SynthesisError
from ailoveshen.domain.value_objects import EmotionState
from ailoveshen.infrastructure.adapters.tts.emotion_style_service import EmotionStyleService


class StyleBertVits2Client(ISpeechSynthesizer):
    """
    Style-Bert-VITS2 の TTS サーバーのインフラ側アダプター。

    出力ポート ISpeechSynthesizer を実装する。
    Style-Bert-VITS2 の FastAPI サーバーと通信し、ドメインの感情を
    Style-Bert-VITS2 のスタイル名に対応させる。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5000,
        timeout_seconds: float = 30.0,
        model_name: str = "default",
        sdp_ratio: float = 0.2,
        noise: float = 0.6,
        noisew: float = 0.8,
        length: float = 1.0,
        emotion_style_service: Optional[EmotionStyleService] = None,
    ) -> None:
        """
        TTS クライアントを初期化する。

        Args:
            host: TTS サーバーのホスト名
            port: TTS サーバーのポート
            timeout_seconds: リクエストのタイムアウト（秒）
            model_name: 合成に使う既定のモデル
            sdp_ratio: 合成の SDP 比率のパラメーター
            noise: 合成のノイズのパラメーター
            noisew: 合成のノイズの重みのパラメーター
            length: 合成の長さの倍率のパラメーター
            emotion_style_service: 感情からスタイルへの対応（既定は組み込みの対応）
        """
        self._base_url = f"http://{host}:{port}"
        self._timeout = timeout_seconds
        self._model_name = model_name
        self._sdp_ratio = sdp_ratio
        self._noise = noise
        self._noisew = noisew
        self._length = length
        self._emotion_style_service = emotion_style_service or EmotionStyleService()
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """HTTP クライアントを初期化し、接続を確かめる。"""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )

        # ヘルスチェックで接続を確かめる
        try:
            response = await self._client.get("/models/info")
            response.raise_for_status()
            logger.info(f"TTS クライアントが {self._base_url} に接続した")
        except httpx.RequestError as e:
            await self._client.aclose()
            self._client = None
            raise ConnectionError(f"Failed to connect to TTS server: {e}") from e

    async def disconnect(self) -> None:
        """HTTP クライアントを閉じる。"""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("TTS クライアントを切断した")

    def is_connected(self) -> bool:
        """クライアントが接続しているかを返す。"""
        return self._client is not None

    async def synthesize(
        self,
        text: str,
        emotion: EmotionState,
        speaker_id: int = 0,
        language: str = "JP",
    ) -> bytes:
        """
        Style-Bert-VITS2 のサーバーで音声を合成する。

        Args:
            text: 合成するテキスト
            emotion: 表現する感情（スタイル名に対応させる）
            speaker_id: 複数話者のモデルでの話者 ID
            language: 言語コード（JP、EN、ZH）

        Returns:
            音声データのバイト列（WAV 形式）

        Raises:
            SynthesisError: 合成に失敗したとき
        """
        if not self._client:
            raise SynthesisError("TTS client not connected. Call connect() first.")

        style = self._emotion_style_service.get_style_for_emotion(emotion)

        try:
            response = await self._client.get(
                "/voice",
                params={
                    "text": text,
                    "model_name": self._model_name,
                    "speaker_id": speaker_id,
                    "style": style,
                    "language": language,
                    "sdp_ratio": self._sdp_ratio,
                    "noise": self._noise,
                    "noisew": self._noisew,
                    "length": self._length,
                },
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "audio" not in content_type and "octet-stream" not in content_type:
                # 想定外の応答。エラーメッセージかもしれない
                raise SynthesisError(
                    f"Unexpected response type: {content_type}. Response: {response.text[:200]}"
                )

            return response.content

        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_detail = e.response.text[:200]
            except Exception:
                pass
            raise SynthesisError(
                f"TTS synthesis failed with status {e.response.status_code}: {error_detail}"
            ) from e
        except httpx.RequestError as e:
            raise SynthesisError(f"TTS connection error: {e}") from e

    async def get_available_styles(self) -> List[str]:
        """
        TTS サーバーから使えるスタイルを取得する。

        Returns:
            今のモデルで使えるスタイル名の一覧。
        """
        if not self._client:
            return ["Neutral"]

        try:
            response = await self._client.get("/models/info")
            response.raise_for_status()
            data = response.json()

            # Style-Bert-VITS2 は style2id の対応を含むモデルの情報を返す
            if self._model_name in data:
                model_info = data[self._model_name]
                style2id = model_info.get("style2id", {})
                return list(style2id.keys()) if style2id else ["Neutral"]

            return ["Neutral"]

        except Exception as e:
            logger.warning(f"TTS サーバーからスタイルを取得できなかった: {e}")
            return ["Neutral"]
