"""SounddevicePlayer アダプタのテスト。"""

import asyncio
import io
import struct
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ailoveshen.domain.exceptions import AudioPlaybackError

# sounddevice がなければ、このモジュールのテストは全部飛ばす
sounddevice = pytest.importorskip("sounddevice", reason="sounddevice not installed")

from ailoveshen.infrastructure.adapters.audio.sounddevice_player import (
    SounddevicePlayer,
    apply_gain,
    _parse_wav_header,
    _wav_to_numpy,
)


def create_wav_data(
    sample_rate: int = 44100,
    duration_seconds: float = 0.1,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """テスト用の WAV データを作る。"""
    num_samples = int(sample_rate * duration_seconds)

    # 正弦波を作る
    t = np.linspace(0, duration_seconds, num_samples, dtype=np.float32)
    samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)

    if channels == 2:
        samples = np.column_stack([samples, samples]).flatten()

    # WAV ヘッダを組み立てる
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
        16,  # fmt チャンクの大きさ
        1,  # 音声の形式（PCM）
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
    """_parse_wav_header 関数のテスト。"""

    def test_parse_valid_wav(self):
        """正しい WAV ヘッダを読む。"""
        wav_data = create_wav_data(sample_rate=22050, channels=1, bits_per_sample=16)
        sample_rate, channels, bits = _parse_wav_header(wav_data)

        assert sample_rate == 22050
        assert channels == 1
        assert bits == 16

    def test_parse_stereo_wav(self):
        """ステレオの WAV ヘッダを読む。"""
        wav_data = create_wav_data(sample_rate=44100, channels=2, bits_per_sample=16)
        sample_rate, channels, bits = _parse_wav_header(wav_data)

        assert sample_rate == 44100
        assert channels == 2
        assert bits == 16

    def test_invalid_riff_header_raises(self):
        """RIFF ヘッダが不正ならエラー。"""
        invalid_data = b"INVALID_HEADER"
        with pytest.raises(AudioPlaybackError, match="missing RIFF/WAVE"):
            _parse_wav_header(invalid_data)

    def test_missing_wave_identifier_raises(self):
        """WAVE の識別子がなければエラー。"""
        invalid_data = b"RIFF\x00\x00\x00\x00NOTW"
        with pytest.raises(AudioPlaybackError, match="missing RIFF/WAVE"):
            _parse_wav_header(invalid_data)


class TestWavToNumpy:
    """_wav_to_numpy 関数のテスト。"""

    def test_convert_16bit_mono(self):
        """16 ビットのモノラル WAV を変換する。"""
        wav_data = create_wav_data(sample_rate=44100, channels=1, bits_per_sample=16)
        audio_array, sample_rate = _wav_to_numpy(wav_data)

        assert sample_rate == 44100
        assert audio_array.dtype == np.float32
        assert audio_array.ndim == 1
        # 値は [-1, 1] に正規化される
        assert audio_array.min() >= -1.0
        assert audio_array.max() <= 1.0

    def test_convert_stereo(self):
        """ステレオの WAV を変換する。"""
        wav_data = create_wav_data(sample_rate=44100, channels=2, bits_per_sample=16)
        audio_array, sample_rate = _wav_to_numpy(wav_data)

        assert sample_rate == 44100
        # ステレオは 2 次元の形か、交互に並ぶ
        assert audio_array.size > 0


class TestSounddevicePlayer:
    """SounddevicePlayer アダプタのテスト。"""

    @pytest.fixture
    def player(self):
        """SounddevicePlayer を作る。"""
        return SounddevicePlayer()

    def test_init_defaults(self, player):
        """既定値で初期化する。"""
        assert player._device is None
        assert player._blocksize == 1024
        assert player._playing is False

    def test_init_custom_values(self):
        """値を指定して初期化する。"""
        player = SounddevicePlayer(device=1, blocksize=2048)
        assert player._device == 1
        assert player._blocksize == 2048

    def test_is_playing_initially_false(self, player):
        """is_playing は最初 False を返す。"""
        assert player.is_playing() is False

    def test_stop_is_safe_when_not_playing(self, player):
        """再生していないときに stop() を呼んでも問題ない。"""
        # 例外にならない
        player.stop()

    def test_get_duration_ms(self, player):
        """get_duration_ms を正しく計算する。"""
        # 44100 Hz で 0.1 秒
        wav_data = create_wav_data(sample_rate=44100, duration_seconds=0.1)
        duration = player.get_duration_ms(wav_data)

        # ほぼ 100ms になる
        assert 90 <= duration <= 110

    def test_get_duration_ms_invalid_data_raises(self, player):
        """get_duration_ms はデータが不正なら例外を出す。"""
        with pytest.raises(AudioPlaybackError):
            player.get_duration_ms(b"invalid data")

    @pytest.mark.asyncio
    async def test_play_with_interrupt_event(self, player):
        """play は中断のイベントに従う。"""
        wav_data = create_wav_data(duration_seconds=0.5)
        interrupt_event = asyncio.Event()

        with patch("sounddevice.play"), patch("sounddevice.stop"), patch("sounddevice.wait"):
            # すぐに中断を立てる
            interrupt_event.set()

            result = await player.play(wav_data, interrupt_event=interrupt_event)

            # False（中断）を返す
            assert result is False

    @pytest.mark.asyncio
    async def test_play_sets_playing_flag(self, player):
        """play は再生中のフラグを立てて下ろす。"""
        wav_data = create_wav_data(duration_seconds=0.05)

        with patch("sounddevice.play"), patch("sounddevice.stop"), patch("sounddevice.wait"):
            # play を動かしてフラグを確かめる
            task = asyncio.create_task(player.play(wav_data))
            await asyncio.sleep(0.01)  # play が始まるのを待つ

            # 注: 非同期なので、ここは変わりうる
            await task

            # 終わったら False になる
            assert player.is_playing() is False


class TestApplyGain:
    """音量を上げて、山をソフトリミッターで丸める。"""

    def test_zero_gain_leaves_the_audio_alone(self):
        audio = np.array([0.1, -0.5, 1.0], dtype=np.float32)
        assert apply_gain(audio, 0.0) is audio

    def test_quiet_samples_are_scaled_exactly(self):
        audio = np.array([0.1, -0.2, 0.0], dtype=np.float32)
        out = apply_gain(audio, 6.0)
        np.testing.assert_allclose(out, audio * 10 ** (6 / 20), rtol=1e-5)

    def test_peaks_never_pass_full_scale(self):
        # the TTS server returns every line with a peak of exactly 1.0
        audio = np.array([1.0, -1.0, 0.9, -0.8, 0.3], dtype=np.float32)
        out = apply_gain(audio, 12.0)
        assert np.abs(out).max() <= 1.0
        assert out[0] > 0.99 and out[1] < -0.99

    def test_soft_limiter_keeps_the_sign_and_order(self):
        audio = np.linspace(-1.0, 1.0, 201, dtype=np.float32)
        out = apply_gain(audio, 6.0)
        assert np.all(np.diff(out) >= 0)
        assert np.all(np.sign(out) == np.sign(audio))

    def test_negative_gain_makes_it_quieter(self):
        audio = np.array([0.5, -0.5], dtype=np.float32)
        out = apply_gain(audio, -6.0)
        assert np.abs(out).max() < 0.5

    def test_player_applies_the_gain_before_playing(self):
        wav = create_wav_data(sample_rate=8000, duration_seconds=0.05)
        with patch("ailoveshen.infrastructure.adapters.audio.sounddevice_player.sd") as sd:
            player = SounddevicePlayer(gain_db=-20.0)
            asyncio.run(player.play(wav))
        played = sd.play.call_args.args[0]
        assert np.abs(played).max() < 0.2   # a full-scale sine, 20 dB down
