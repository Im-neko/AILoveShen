"""Irodori-TTS-Server（OpenAI 互換の音声合成 API）への HTTP クライアントのアダプター。

docs/setup/irodori_tts.md。サーバーは Mac では Docker の外で動かす（Docker からは Apple Silicon の
GPU（MPS）が使えない）。声は参照音声（サーバーの voices/ に置いたもの）から学習なしでまねる。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import httpx
from loguru import logger

from ailoveshen.application.ports.output.speech_synthesizer import ISpeechSynthesizer
from ailoveshen.domain.exceptions import SynthesisError
from ailoveshen.domain.value_objects import EmotionState, EmotionType

MODEL_ID = "irodori-tts"


class IrodoriTtsClient(ISpeechSynthesizer):
    """
    Irodori-TTS-Server のインフラ側アダプター（出力ポート ISpeechSynthesizer）。

    `POST /v1/audio/speech` に WAV を頼む。感情は、設定があれば話し方の説明（caption）にする
    （強さが `caption_min_intensity` 以上のときだけ。中立や弱い感情は参照音声のままの話し方）。
    seed を決めておくと、同じ文はいつも同じ声になる（安定性）。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8088,
        timeout_seconds: float = 60.0,
        voice: str = "shen",
        speed: float = 1.0,
        num_steps: Optional[int] = None,
        seed: Optional[int] = 1234,
        cfg_scale_text: Optional[float] = None,
        cfg_scale_speaker: Optional[float] = None,
        emotion_captions: Optional[Mapping[EmotionType, str]] = None,
        caption_min_intensity: float = 0.6,
        api_key: str = "",
    ) -> None:
        """
        Args:
            host: サーバーのホスト名
            port: サーバーのポート（既定 8088）
            timeout_seconds: 1 回の合成を待つ秒数（最初の 1 回はモデルの読み込みを含む）
            voice: 声の ID（サーバーの voices/ のファイル名か voices.json の名前）
            speed: 話す速さ（0.25〜4.0）
            num_steps: サンプリングの段数（None: サーバーとモデルの既定）
            seed: 乱数の種（None: 毎回変わる）。決めておくと同じ文は同じ声になる
            cfg_scale_text: テキストへの寄せ方（None: 既定）
            cfg_scale_speaker: 参照音声への寄せ方（None: 既定）。上げると声が似る
            emotion_captions: 感情 → 話し方の説明。空なら感情は声に出さない
            caption_min_intensity: この強さ以上の感情だけ説明を付ける
            api_key: サーバーの IRODORI_API_KEY（空: なし）
        """
        self._base_url = f"http://{host}:{port}"
        self._timeout = timeout_seconds
        self._voice = voice
        self._speed = speed
        self._num_steps = num_steps
        self._seed = seed
        self._cfg_text = cfg_scale_text
        self._cfg_speaker = cfg_scale_speaker
        self._captions = dict(emotion_captions or {})
        self._caption_min = caption_min_intensity
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """HTTP クライアントを作り、サーバーが動いているか（/health）と声があるかを確かめる。"""
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, headers=headers
        )
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            await self._client.aclose()
            self._client = None
            raise ConnectionError(f"Failed to connect to Irodori-TTS server: {e}") from e
        await self._check_voice()
        logger.info(f"Irodori-TTS に {self._base_url} で接続した（声 {self._voice}）")

    async def _check_voice(self) -> None:
        """声が登録されていなければ警告する（合成は既定の声か失敗になる）。"""
        assert self._client is not None
        try:
            response = await self._client.get("/v1/audio/voices")
            response.raise_for_status()
            data = response.json()
        except Exception as e:  # noqa: BLE001 - 一覧が読めなくても合成は試せる
            logger.debug(f"Irodori-TTS の声の一覧を読めなかった: {e}")
            return
        voices = data.get("data", data) if isinstance(data, dict) else data
        ids = {str(v.get("id", v.get("voice_id", ""))) if isinstance(v, dict) else str(v) for v in voices or []}
        if ids and self._voice not in ids:
            logger.warning(
                f"Irodori-TTS に声 {self._voice!r} がない（あるもの: {', '.join(sorted(ids))}）。"
                "tools/irodori_voice.py で参照音声を用意する"
            )

    async def disconnect(self) -> None:
        """HTTP クライアントを閉じる。"""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("Irodori-TTS との接続を閉じた")

    def is_connected(self) -> bool:
        """クライアントが接続しているかを返す。"""
        return self._client is not None

    def caption_for(self, emotion: EmotionState) -> Optional[str]:
        """感情の話し方の説明（付けないときは None）。"""
        if emotion.intensity < self._caption_min:
            return None
        return self._captions.get(emotion.primary) or None

    def request_body(self, text: str, emotion: EmotionState) -> dict[str, Any]:
        """合成の要求の本文。"""
        irodori: dict[str, Any] = {}
        for key, value in (
            ("num_steps", self._num_steps),
            ("seed", self._seed),
            ("cfg_scale_text", self._cfg_text),
            ("cfg_scale_speaker", self._cfg_speaker),
            ("caption", self.caption_for(emotion)),
        ):
            if value is not None:
                irodori[key] = value
        body: dict[str, Any] = {
            "model": MODEL_ID,
            "input": text,
            "voice": self._voice,
            "response_format": "wav",
            "speed": self._speed,
        }
        if irodori:
            body["irodori"] = irodori
        return body

    async def synthesize(
        self,
        text: str,
        emotion: EmotionState,
        speaker_id: int = 0,
        language: str = "JP",
    ) -> bytes:
        """
        Irodori-TTS で音声を合成する（WAV）。speaker_id と language は使わない（声は voice、
        日本語専用のモデル）。

        Raises:
            SynthesisError: 合成に失敗したとき
        """
        if not self._client:
            raise SynthesisError("TTS client not connected. Call connect() first.")
        try:
            response = await self._client.post(
                "/v1/audio/speech", json=self.request_body(text, emotion)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:200] if e.response is not None else ""
            raise SynthesisError(
                f"Irodori-TTS synthesis failed with status {e.response.status_code}: {detail}"
            ) from e
        except httpx.RequestError as e:
            raise SynthesisError(f"Irodori-TTS connection error: {e}") from e
        data = response.content
        if data[:4] != b"RIFF":
            raise SynthesisError(
                f"Irodori-TTS returned no WAV ({response.headers.get('content-type', '')}): "
                f"{response.text[:200]}"
            )
        return data
