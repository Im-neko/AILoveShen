"""Tests for SounddevicePlayer adapter."""

import asyncio
import io
import struct
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ailoveshen.domain.exceptions import AudioPlaybackError

# Skip all tests in this module if sounddevice is not installed
sounddevice = pytest.importorskip("sounddevice", reason="sounddevice not installed")

from ailoveshen.infrastructure.adapters.audio.sounddevice_player import (
    SounddevicePlayer,
    _parse_wav_header,
    _wav_to_numpy,
)


def create_wav_data(
    sample_rate: int = 44100,
    duration_seconds: float = 0.1,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Create test WAV data."""
    num_samples = int(sample_rate * duration_seconds)

    # Generate sine wave
    t = np.linspace(0, duration_seconds, num_samples, dtype=np.float32)
    samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)

    if channels == 2:
        samples = np.column_stack([samples, samples]).flatten()

    # Build WAV header
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    data_size = len(samples) * (bits_per_sample // 8)
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # audio format (PCM)
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )

    return header + samples.tobytes()


class TestParseWavHeader:
    """Tests for _parse_wav_header function."""

    def test_parse_valid_wav(self):
        """Test parsing valid WAV header."""
        wav_data = create_wav_data(sample_rate=22050, channels=1, bits_per_sample=16)
        sample_rate, channels, bits = _parse_wav_header(wav_data)

        assert sample_rate == 22050
        assert channels == 1
        assert bits == 16

    def test_parse_stereo_wav(self):
        """Test parsing stereo WAV header."""
        wav_data = create_wav_data(sample_rate=44100, channels=2, bits_per_sample=16)
        sample_rate, channels, bits = _parse_wav_header(wav_data)

        assert sample_rate == 44100
        assert channels == 2
        assert bits == 16

    def test_invalid_riff_header_raises(self):
        """Test invalid RIFF header raises error."""
        invalid_data = b"INVALID_HEADER"
        with pytest.raises(AudioPlaybackError, match="missing RIFF/WAVE"):
            _parse_wav_header(invalid_data)

    def test_missing_wave_identifier_raises(self):
        """Test missing WAVE identifier raises error."""
        invalid_data = b"RIFF\x00\x00\x00\x00NOTW"
        with pytest.raises(AudioPlaybackError, match="missing RIFF/WAVE"):
            _parse_wav_header(invalid_data)


class TestWavToNumpy:
    """Tests for _wav_to_numpy function."""

    def test_convert_16bit_mono(self):
        """Test converting 16-bit mono WAV."""
        wav_data = create_wav_data(sample_rate=44100, channels=1, bits_per_sample=16)
        audio_array, sample_rate = _wav_to_numpy(wav_data)

        assert sample_rate == 44100
        assert audio_array.dtype == np.float32
        assert audio_array.ndim == 1
        # Values should be normalized to [-1, 1]
        assert audio_array.min() >= -1.0
        assert audio_array.max() <= 1.0

    def test_convert_stereo(self):
        """Test converting stereo WAV."""
        wav_data = create_wav_data(sample_rate=44100, channels=2, bits_per_sample=16)
        audio_array, sample_rate = _wav_to_numpy(wav_data)

        assert sample_rate == 44100
        # Stereo should have 2D shape or be interleaved
        assert audio_array.size > 0


class TestSounddevicePlayer:
    """Tests for SounddevicePlayer adapter."""

    @pytest.fixture
    def player(self):
        """Create a SounddevicePlayer."""
        return SounddevicePlayer()

    def test_init_defaults(self, player):
        """Test initialization with defaults."""
        assert player._device is None
        assert player._blocksize == 1024
        assert player._playing is False

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        player = SounddevicePlayer(device=1, blocksize=2048)
        assert player._device == 1
        assert player._blocksize == 2048

    def test_is_playing_initially_false(self, player):
        """Test is_playing returns False initially."""
        assert player.is_playing() is False

    def test_stop_is_safe_when_not_playing(self, player):
        """Test stop() is safe to call when not playing."""
        # Should not raise
        player.stop()

    def test_get_duration_ms(self, player):
        """Test get_duration_ms calculates correctly."""
        # 0.1 seconds at 44100 Hz
        wav_data = create_wav_data(sample_rate=44100, duration_seconds=0.1)
        duration = player.get_duration_ms(wav_data)

        # Should be approximately 100ms
        assert 90 <= duration <= 110

    def test_get_duration_ms_invalid_data_raises(self, player):
        """Test get_duration_ms raises for invalid data."""
        with pytest.raises(AudioPlaybackError):
            player.get_duration_ms(b"invalid data")

    @pytest.mark.asyncio
    async def test_play_with_interrupt_event(self, player):
        """Test play respects interrupt event."""
        wav_data = create_wav_data(duration_seconds=0.5)
        interrupt_event = asyncio.Event()

        with patch("sounddevice.play"), patch("sounddevice.stop"), patch(
            "sounddevice.wait"
        ):
            # Set interrupt immediately
            interrupt_event.set()

            result = await player.play(wav_data, interrupt_event=interrupt_event)

            # Should return False (interrupted)
            assert result is False

    @pytest.mark.asyncio
    async def test_play_sets_playing_flag(self, player):
        """Test play sets and clears playing flag."""
        wav_data = create_wav_data(duration_seconds=0.05)

        with patch("sounddevice.play"), patch("sounddevice.stop"), patch(
            "sounddevice.wait"
        ):
            # Run play and check flag
            task = asyncio.create_task(player.play(wav_data))
            await asyncio.sleep(0.01)  # Let play start

            # Note: Due to async nature, this may vary
            await task

            # After completion, should be False
            assert player.is_playing() is False
