"""Sounddevice-based audio player adapter."""

from __future__ import annotations

import asyncio
import io
import struct
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd
from loguru import logger

from ailoveshen.core.exceptions import AudioPlaybackError
from ailoveshen.tts.application.ports.output.audio_player import IAudioPlayer


def _parse_wav_header(data: bytes) -> Tuple[int, int, int]:
    """
    Parse WAV header to extract audio parameters.

    Args:
        data: WAV file data

    Returns:
        Tuple of (sample_rate, num_channels, bits_per_sample)

    Raises:
        AudioPlaybackError: If WAV header is invalid
    """
    try:
        # Check RIFF header
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise AudioPlaybackError("Invalid WAV format: missing RIFF/WAVE header")

        # Find fmt chunk
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
    Convert WAV bytes to numpy array.

    Args:
        data: WAV file data

    Returns:
        Tuple of (audio_array as float32, sample_rate)

    Raises:
        AudioPlaybackError: If conversion fails
    """
    try:
        # Use scipy.io.wavfile if available, fall back to manual parsing
        try:
            from scipy.io import wavfile

            sample_rate, audio_array = wavfile.read(io.BytesIO(data))
        except ImportError:
            # Manual parsing fallback
            sample_rate, num_channels, bits_per_sample = _parse_wav_header(data)

            # Find data chunk
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

            # Convert to numpy array
            if bits_per_sample == 16:
                audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
            elif bits_per_sample == 32:
                audio_array = np.frombuffer(audio_bytes, dtype=np.int32)
            elif bits_per_sample == 8:
                audio_array = np.frombuffer(audio_bytes, dtype=np.uint8)
            else:
                raise AudioPlaybackError(
                    f"Unsupported bits per sample: {bits_per_sample}"
                )

            if num_channels > 1:
                audio_array = audio_array.reshape(-1, num_channels)

        # Normalize to float32 [-1, 1]
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
    Infrastructure adapter for audio playback using sounddevice.

    Implements IAudioPlayer output port.
    Uses sounddevice library for cross-platform audio output.
    """

    def __init__(
        self,
        device: Optional[int] = None,
        blocksize: int = 1024,
    ) -> None:
        """
        Initialize the audio player.

        Args:
            device: Audio output device ID (None = default device)
            blocksize: Block size for audio streaming
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
        Play audio data with interrupt support.

        Args:
            audio_data: Audio data to play (WAV format)
            interrupt_event: Event to monitor for interruption

        Returns:
            True if playback completed, False if interrupted
        """
        try:
            # Convert WAV to numpy array
            audio_array, sample_rate = _wav_to_numpy(audio_data)

            self._playing = True
            self._stop_requested = False

            # Calculate duration
            duration = len(audio_array) / sample_rate

            # Start playback
            sd.play(audio_array, sample_rate, device=self._device)

            # Wait for playback with interrupt checking
            elapsed = 0.0
            check_interval = 0.05  # 50ms intervals for responsive interrupts

            while elapsed < duration:
                # Check for external interrupt
                if interrupt_event and interrupt_event.is_set():
                    sd.stop()
                    logger.debug("Audio playback interrupted by event")
                    return False

                # Check for internal stop request
                if self._stop_requested:
                    sd.stop()
                    logger.debug("Audio playback stopped by request")
                    return False

                await asyncio.sleep(check_interval)
                elapsed += check_interval

            # Wait for any remaining audio
            sd.wait()
            return True

        except asyncio.CancelledError:
            sd.stop()
            raise
        except AudioPlaybackError:
            raise
        except Exception as e:
            logger.error(f"Audio playback error: {e}")
            raise AudioPlaybackError(f"Playback failed: {e}") from e
        finally:
            self._playing = False
            self._stop_requested = False

    def stop(self) -> None:
        """Stop current playback immediately."""
        self._stop_requested = True
        try:
            sd.stop()
        except Exception as e:
            logger.warning(f"Error stopping audio: {e}")

    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self._playing

    def get_duration_ms(self, audio_data: bytes) -> int:
        """
        Get duration of audio data in milliseconds.

        Args:
            audio_data: Audio data (WAV format)

        Returns:
            Duration in milliseconds

        Raises:
            AudioPlaybackError: If audio data is invalid
        """
        try:
            audio_array, sample_rate = _wav_to_numpy(audio_data)
            duration_seconds = len(audio_array) / sample_rate
            return int(duration_seconds * 1000)
        except Exception as e:
            if isinstance(e, AudioPlaybackError):
                raise
            raise AudioPlaybackError(f"Failed to get audio duration: {e}") from e
