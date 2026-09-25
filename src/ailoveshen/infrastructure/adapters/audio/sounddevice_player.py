"""sounddevice で音声を再生するアダプター。"""

from __future__ import annotations

import asyncio
import io
import struct
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd
from loguru import logger

from ailoveshen.application.ports.output.audio_player import IAudioPlayer
from ailoveshen.domain.exceptions import AudioPlaybackError


def _parse_wav_header(data: bytes) -> Tuple[int, int, int]:
    """
    WAV のヘッダーを解析して音声のパラメーターを取り出す。

    Args:
        data: WAV ファイルのデータ

    Returns:
        (sample_rate, num_channels, bits_per_sample) のタプル

    Raises:
        AudioPlaybackError: WAV のヘッダーが不正なとき
    """
    try:
        # RIFF のヘッダーを確かめる
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise AudioPlaybackError("Invalid WAV format: missing RIFF/WAVE header")

        # fmt チャンクを探す
        pos = 12
        while pos < len(data) - 8:
            chunk_id = data[pos : pos + 4]
            chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]

            if chunk_id == b"fmt ":
                if chunk_size < 16:
                    raise AudioPlaybackError("Invalid fmt chunk size")

                fmt_data = data[pos + 8 : pos + 8 + chunk_size]
                audio_format = struct.unpack("<H", fmt_data[0:2])[0]
                num_channels = struct.unpack("<H", fmt_data[2:4])[0]
                sample_rate = struct.unpack("<I", fmt_data[4:8])[0]
                bits_per_sample = struct.unpack("<H", fmt_data[14:16])[0]

                if audio_format != 1:  # PCM
                    raise AudioPlaybackError(
                        f"Unsupported audio format: {audio_format} (only PCM supported)"
                    )

                return sample_rate, num_channels, bits_per_sample

            pos += 8 + chunk_size

        raise AudioPlaybackError("fmt chunk not found in WAV data")

    except struct.error as e:
        raise AudioPlaybackError(f"Failed to parse WAV header: {e}") from e


def _wav_to_numpy(data: bytes) -> Tuple[np.ndarray, int]:
    """
    WAV のバイト列を numpy の配列に変換する。

    Args:
        data: WAV ファイルのデータ

    Returns:
        (float32 の audio_array, sample_rate) のタプル

    Raises:
        AudioPlaybackError: 変換に失敗したとき
    """
    try:
        # scipy.io.wavfile があれば使い、なければ自前で解析する
        try:
            from scipy.io import wavfile

            sample_rate, audio_array = wavfile.read(io.BytesIO(data))
        except ImportError:
            # 自前の解析（代わり）
            sample_rate, num_channels, bits_per_sample = _parse_wav_header(data)

            # data チャンクを探す
            pos = 12
            while pos < len(data) - 8:
                chunk_id = data[pos : pos + 4]
                chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]

                if chunk_id == b"data":
                    audio_bytes = data[pos + 8 : pos + 8 + chunk_size]
                    break

                pos += 8 + chunk_size
            else:
                raise AudioPlaybackError("data chunk not found in WAV data")

            # numpy の配列に変換する
            if bits_per_sample == 16:
                audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
            elif bits_per_sample == 32:
                audio_array = np.frombuffer(audio_bytes, dtype=np.int32)
            elif bits_per_sample == 8:
                audio_array = np.frombuffer(audio_bytes, dtype=np.uint8)
            else:
                raise AudioPlaybackError(f"Unsupported bits per sample: {bits_per_sample}")

            if num_channels > 1:
                audio_array = audio_array.reshape(-1, num_channels)

        # float32 の [-1, 1] に正規化する
        if audio_array.dtype == np.int16:
            audio_array = audio_array.astype(np.float32) / 32768.0
        elif audio_array.dtype == np.int32:
            audio_array = audio_array.astype(np.float32) / 2147483648.0
        elif audio_array.dtype == np.uint8:
            audio_array = (audio_array.astype(np.float32) - 128.0) / 128.0
        elif audio_array.dtype != np.float32:
            audio_array = audio_array.astype(np.float32)

        return audio_array, sample_rate

    except Exception as e:
        if isinstance(e, AudioPlaybackError):
            raise
        raise AudioPlaybackError(f"Failed to convert WAV to numpy: {e}") from e


class SounddevicePlayer(IAudioPlayer):
    """
    sounddevice で音声を再生するインフラ側アダプター。

    出力ポート IAudioPlayer を実装する。
    クロスプラットフォームで音声を出すため、sounddevice ライブラリを使う。
    """

    def __init__(
        self,
        device: Optional[int] = None,
        blocksize: int = 1024,
    ) -> None:
        """
        音声プレイヤーを初期化する。

        Args:
            device: 音声の出力デバイスの ID（None なら既定のデバイス）
            blocksize: 音声のストリーミングのブロックサイズ
        """
        self._device = device
        self._blocksize = blocksize
        self._playing = False
        self._stop_requested = False

    async def play(
        self,
        audio_data: bytes,
        interrupt_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """
        割り込みに対応して音声データを再生する。

        Args:
            audio_data: 再生する音声データ（WAV 形式）
            interrupt_event: 割り込みを見張るイベント

        Returns:
            再生し終えたら True、割り込まれたら False
        """
        try:
            # WAV を numpy の配列に変換する
            audio_array, sample_rate = _wav_to_numpy(audio_data)

            self._playing = True
            self._stop_requested = False

            # 長さを計算する
            duration = len(audio_array) / sample_rate

            # 再生を始める
            sd.play(audio_array, sample_rate, device=self._device)

            # 割り込みを確かめながら再生を待つ
            elapsed = 0.0
            check_interval = 0.05  # 割り込みにすぐ応じられるよう 50ms ごと

            while elapsed < duration:
                # 外からの割り込みを確かめる
                if interrupt_event and interrupt_event.is_set():
                    sd.stop()
                    logger.debug("イベントで音声の再生を中断した")
                    return False

                # 内部からの停止の要求を確かめる
                if self._stop_requested:
                    sd.stop()
                    logger.debug("要求で音声の再生を止めた")
                    return False

                await asyncio.sleep(check_interval)
                elapsed += check_interval

            # 残りの音声を待つ
            sd.wait()
            return True

        except asyncio.CancelledError:
            sd.stop()
            raise
        except AudioPlaybackError:
            raise
        except Exception as e:
            logger.error(f"音声の再生でエラー: {e}")
            raise AudioPlaybackError(f"Playback failed: {e}") from e
        finally:
            self._playing = False
            self._stop_requested = False

    def stop(self) -> None:
        """今の再生をすぐに止める。"""
        self._stop_requested = True
        try:
            sd.stop()
        except Exception as e:
            logger.warning(f"音声を止めるときにエラー: {e}")

    def is_playing(self) -> bool:
        """今再生中かを返す。"""
        return self._playing

    def get_duration_ms(self, audio_data: bytes) -> int:
        """
        音声データの長さをミリ秒で返す。

        Args:
            audio_data: 音声データ（WAV 形式）

        Returns:
            長さ（ミリ秒）

        Raises:
            AudioPlaybackError: 音声データが不正なとき
        """
        try:
            audio_array, sample_rate = _wav_to_numpy(audio_data)
            duration_seconds = len(audio_array) / sample_rate
            return int(duration_seconds * 1000)
        except Exception as e:
            if isinstance(e, AudioPlaybackError):
                raise
            raise AudioPlaybackError(f"Failed to get audio duration: {e}") from e
