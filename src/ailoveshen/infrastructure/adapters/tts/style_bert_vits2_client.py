"""Style-Bert-VITS2 HTTP client adapter."""

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
    Infrastructure adapter for Style-Bert-VITS2 TTS server.

    Implements ISpeechSynthesizer output port.
    Communicates with the Style-Bert-VITS2 FastAPI server and maps
    domain emotions to Style-Bert-VITS2 style names.
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
        Initialize the TTS client.

        Args:
            host: TTS server hostname
            port: TTS server port
            timeout_seconds: Request timeout in seconds
            model_name: Default model to use for synthesis
            sdp_ratio: SDP ratio parameter for synthesis
            noise: Noise parameter for synthesis
            noisew: Noise weight parameter for synthesis
            length: Length scale parameter for synthesis
            emotion_style_service: Emotion to style mapping (defaults to built-in map)
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
        """Initialize HTTP client and verify connection."""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )

        # Verify connection with a health check
        try:
            response = await self._client.get("/models/info")
            response.raise_for_status()
            logger.info(f"TTS client connected to {self._base_url}")
        except httpx.RequestError as e:
            await self._client.aclose()
            self._client = None
            raise ConnectionError(f"Failed to connect to TTS server: {e}") from e

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("TTS client disconnected")

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._client is not None

    async def synthesize(
        self,
        text: str,
        emotion: EmotionState,
        speaker_id: int = 0,
        language: str = "JP",
    ) -> bytes:
        """
        Synthesize speech using Style-Bert-VITS2 server.

        Args:
            text: Text to synthesize
            emotion: Emotion to express (mapped to a style name)
            speaker_id: Speaker ID for multi-speaker models
            language: Language code (JP, EN, ZH)

        Returns:
            Audio data as bytes (WAV format)

        Raises:
            SynthesisError: If synthesis fails
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
                # Unexpected response, might be an error message
                raise SynthesisError(
                    f"Unexpected response type: {content_type}. "
                    f"Response: {response.text[:200]}"
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
        Get available styles from the TTS server.

        Returns:
            List of available style names for the current model.
        """
        if not self._client:
            return ["Neutral"]

        try:
            response = await self._client.get("/models/info")
            response.raise_for_status()
            data = response.json()

            # Style-Bert-VITS2 returns model info with style2id mapping
            if self._model_name in data:
                model_info = data[self._model_name]
                style2id = model_info.get("style2id", {})
                return list(style2id.keys()) if style2id else ["Neutral"]

            return ["Neutral"]

        except Exception as e:
            logger.warning(f"Failed to get styles from TTS server: {e}")
            return ["Neutral"]
