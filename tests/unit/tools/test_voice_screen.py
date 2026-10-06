"""tools/voice_screen.py の、別の人の声に流れたクリップの判定のテスト。"""

import importlib.util
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "voice_screen", Path(__file__).resolve().parents[3] / "tools" / "voice_screen.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def test_a_clip_far_from_the_rest_is_an_outlier():
    similarity = [0.88, 0.90, 0.87, 0.89, 0.91, 0.88, 0.55]
    assert tool.outliers(similarity, mads=4.0, floor=0.3) == [False] * 6 + [True]


def test_ordinary_spread_is_not_flagged():
    similarity = [0.80, 0.86, 0.90, 0.84, 0.88, 0.82, 0.78]
    assert not any(tool.outliers(similarity, mads=4.0, floor=0.3))


def test_below_the_floor_is_flagged_even_when_everything_is_low():
    similarity = [0.2, 0.22, 0.21, 0.2, 0.23]
    assert all(tool.outliers(similarity, mads=4.0, floor=0.3))


def test_identical_clips_are_not_flagged():
    assert not any(tool.outliers([0.9] * 5, mads=4.0, floor=0.3))
