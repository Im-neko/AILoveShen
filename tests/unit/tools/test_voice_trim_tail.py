"""tools/voice_trim_tail.py の、発話のあとの雑音を切る判定のテスト。"""

import importlib.util
import sys
from pathlib import Path

import numpy as np

SPEC = importlib.util.spec_from_file_location(
    "voice_trim_tail", Path(__file__).resolve().parents[3] / "tools" / "voice_trim_tail.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)

RATE = 24000


def _voice(seconds: float, amplitude: float = 0.2) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return amplitude * np.sin(2 * np.pi * 220 * t)


def _noise(seconds: float, amplitude: float) -> np.ndarray:
    # Irodori-TTS の末尾の雑音に近い、高域（6kHz 超）が 2 割ほどの雑音: 白色雑音を 4 点で平均する
    white = np.random.default_rng(0).standard_normal(int(RATE * seconds))
    smooth = np.convolve(white, np.ones(4) / 4, mode="same")
    return amplitude * smooth / smooth.std()


def _pcm(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def test_noise_after_the_voice_is_cut():
    voice = _voice(1.0)
    samples = _pcm(np.concatenate([voice, np.zeros(int(RATE * 0.1)), _noise(0.4, 0.05)]))
    end = tool.speech_end(samples, RATE, margin=0.08)
    assert 1.0 * RATE <= end <= (1.0 + 0.04 + 0.08) * RATE + 1500
    assert end < len(samples) - 0.2 * RATE


def test_nothing_to_cut_when_the_voice_runs_to_the_end():
    samples = _pcm(_voice(1.0))
    assert tool.speech_end(samples, RATE) >= len(samples) - 0.05 * RATE


def test_silence_only_is_left_alone():
    samples = np.zeros(RATE, dtype=np.int16)
    assert tool.speech_end(samples, RATE) == len(samples)


def test_trim_fades_the_end():
    samples = _pcm(np.concatenate([_voice(1.0), _noise(0.4, 0.05)]))
    out = tool.trim(samples, RATE, margin=0.08, fade=0.03)
    assert len(out) < len(samples)
    assert abs(out[-1]) < 1.0
