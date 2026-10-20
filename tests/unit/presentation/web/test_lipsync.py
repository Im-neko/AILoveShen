"""口の動き（js/lipsync.mjs）のテスト。node で関数を呼び、数値を確かめる。"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
MODULE = Path(__file__).resolve().parents[4] / "src/ailoveshen/presentation/web/js/lipsync.mjs"

pytestmark = pytest.mark.skipif(NODE is None, reason="node がない")


def run(script: str):
    code = f"import * as L from '{MODULE.as_uri()}'\n{script}"
    out = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_between_different_vowels_the_mouth_never_closes():
    # 「あいうえお」を細かく刻んで、最初と最後の半分の音を除いた間の開きの合計の最小を見る
    low = run(
        """
        const seq = L.visemes('あいうえお')
        let min = 9
        for (let k = 0.5; k <= seq.length - 0.5; k += 0.01) {
          const m = L.mouthAt(seq, k)
          min = Math.min(min, Object.values(m).reduce((a, b) => a + b, 0))
        }
        console.log(JSON.stringify(min))
        """
    )
    assert low > 0.9 * 0.85  # 以前は境目で 0.55 倍まで下がり、そのうえ別の母音へ切り替わる間も閉じかけていた


def test_a_repeated_vowel_stays_open_with_only_a_small_dip():
    low = run(
        """
        const seq = L.visemes('ああああ')
        let min = 9
        for (let k = 0.5; k <= seq.length - 0.5; k += 0.01) min = Math.min(min, L.mouthAt(seq, k).aa)
        console.log(JSON.stringify(min))
        """
    )
    assert low >= 0.9 * (1 - 0.12) - 1e-9


def test_a_pause_and_the_start_and_end_close_the_mouth():
    r = run(
        """
        const seq = L.visemes('あ、あ')
        const total = (k) => Object.values(L.mouthAt(seq, k)).reduce((a, b) => a + b, 0)
        console.log(JSON.stringify({ start: total(0), pause: total(2.5), end: total(seq.length), voiced: total(0.5) }))
        """
    )
    assert r["start"] == 0 and r["end"] == 0 and r["pause"] < 0.05 and r["voiced"] > 0.8


def test_the_mouth_moves_smoothly_with_no_jumps():
    jump = run(
        """
        const seq = L.visemes('こんにちは、きょうはいいてんきですね')
        let prev = L.mouthAt(seq, 0), worst = 0
        for (let k = 0.005; k <= seq.length; k += 0.005) {
          const m = L.mouthAt(seq, k)
          for (const v of L.OPEN) worst = Math.max(worst, Math.abs(m[v] - prev[v]))
          prev = m
        }
        console.log(JSON.stringify(worst))
        """
    )
    assert jump < 0.03  # 0.005 音ぶん進めたときの 1 歩の変化


def test_fast_speech_is_merged_into_fewer_slower_sounds():
    r = run(
        """
        const seq = L.visemes('あいうえおかきくけこ')
        const fast = L.pace(seq, 0.08)
        const slow = L.pace(seq, 0.2)
        console.log(JSON.stringify({ n: seq.length, fastN: fast.seq.length, fastPer: fast.per, slowN: slow.seq.length, slowPer: slow.per }))
        """
    )
    assert r["fastN"] == r["n"] // 2 and abs(r["fastPer"] - 0.16) < 1e-9  # 速すぎる: 2 音を 1 つにして遅くする
    assert r["slowN"] == r["n"] and r["slowPer"] == 0.2  # ゆっくりなら、そのまま


def test_merging_keeps_the_pause_and_prefers_a_sound_over_a_gap():
    r = run(
        """
        const seq = [{ v: 'aa', w: 0.9 }, { v: null, w: 0 }, { v: null, w: 0 }, { v: null, w: 0 }, { v: 'ih', w: 0.9 }, { v: 'ou', w: 0.25 }]
        console.log(JSON.stringify(L.pace(seq, 0.07).seq.map((s) => s.v)))
        """
    )
    assert r == ["aa", None, "ih"]
