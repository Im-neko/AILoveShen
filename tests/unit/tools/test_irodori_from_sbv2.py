"""tools/irodori_from_sbv2.py の、学習に入れる音声の判定のテスト。"""

import importlib.util
import io
import math
import struct
import sys
import wave
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "irodori_from_sbv2", Path(__file__).resolve().parents[3] / "tools" / "irodori_from_sbv2.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool  # dataclass が自分のモジュールを引く
SPEC.loader.exec_module(tool)


def _wav(seconds: float, amplitude: int = 8000, clip: bool = False) -> bytes:
    """正弦波。clip: 振幅の 2 倍を最大値で切る（本当の音割れ）。"""
    b = io.BytesIO()
    gain = 2.0 if clip else 1.0
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(
            b"".join(
                struct.pack("<h", max(-32768, min(32767, int(gain * amplitude * math.sin(i * 0.05)))))
                for i in range(int(8000 * seconds))
            )
        )
    return b.getvalue()


def test_wav_stats_reads_length_peak_and_loudness():
    seconds, peak, rms, clipped = tool.wav_stats(_wav(1.5))
    assert abs(seconds - 1.5) < 0.01
    assert 0.2 < peak < 0.26
    assert rms > 0.1
    assert not clipped


def test_full_scale_is_not_clipping_but_a_flat_top_is():
    """Style-Bert-VITS2 は最大値ちょうどに揃えて返す: それは音割れではない（全部捨てていた）。"""
    assert not tool.wav_stats(_wav(1.0, amplitude=32767))[3]
    assert tool.wav_stats(_wav(1.0, amplitude=32767, clip=True))[3]


def test_check_keeps_a_normal_reading_and_names_why_others_are_left_out():
    line = tool.Line("EMOTION100_001", "えっ嘘でしょ。", "neutral", "エッウソデショ。")
    assert tool.check(line, *tool.wav_stats(_wav(1.2))) == ""
    long = tool.Line("RECITATION324_001", "女の子がキッキッ嬉しそう。", "neutral", "オンナノコガキッキッウレシソー。")
    assert "読みの速さ" in tool.check(long, *tool.wav_stats(_wav(0.8)))  # 読み飛ばし（15 カナを 0.8 秒）
    assert "読みの速さ" in tool.check(line, *tool.wav_stats(_wav(4.0)))  # 間延び
    assert tool.check(line, *tool.wav_stats(_wav(1.2, amplitude=32767))) == ""
    assert tool.check(line, *tool.wav_stats(_wav(1.2, amplitude=32767, clip=True))) == "音割れ"
    assert tool.check(line, *tool.wav_stats(_wav(1.2, amplitude=50))) == "ほぼ無音"
    assert "長さ" in tool.check(tool.Line("N001", "x", "neutral"), 25.0, 0.5, 0.1)


def test_read_lines_takes_both_scripts():
    lines = tool.read_lines()
    ids = {line.id for line in lines}
    assert {"N001", "L004", "EMOTION100_001", "RECITATION324_324"} <= ids
    assert next(line for line in lines if line.id == "EMOTION100_001").kana
