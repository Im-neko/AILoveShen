"""tools/voice_dataset.py の、複数のフォルダーを学習データにまとめる流れのテスト。"""

import importlib.util
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "voice_dataset", Path(__file__).resolve().parents[3] / "tools" / "voice_dataset.py"
)
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF")
    return path


def _run(tmp_path: Path, monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["voice_dataset.py", *argv, "--data", str(tmp_path / "Data")])
    return tool.main()


def test_find_wavs_skips_rejected_and_previous(tmp_path):
    _touch(tmp_path / "N001.wav")
    _touch(tmp_path / "audio_rejected" / "N002.wav")
    _touch(tmp_path / "_previous" / "N003.wav")
    assert list(tool.find_wavs(tmp_path)) == ["N001"]


def test_same_number_in_another_folder_is_kept_under_its_own_name(tmp_path, monkeypatch):
    main = tmp_path / "shen_irodori" / "audio"
    happy = tmp_path / "shen_sbv2_happy" / "audio"
    _touch(main / "H001.wav")
    _touch(happy / "H001.wav")
    code = _run(tmp_path, monkeypatch, str(main), "--model", "m", "--neutral-only", "--extra", f"Happy={happy}")
    assert code == 0
    raw = tmp_path / "Data" / "m" / "raw"
    assert (raw / "H001.wav").exists()
    assert (raw / "Happy" / "shen_sbv2_happy_H001.wav").exists()
    esd = (tmp_path / "Data" / "m" / "esd.list").read_text(encoding="utf-8").splitlines()
    assert [r.split("|")[0] for r in esd] == ["H001.wav", "Happy/shen_sbv2_happy_H001.wav"]
    assert esd[0].split("|", 3)[3] == esd[1].split("|", 3)[3]  # 同じ台本の文


def test_neutral_only_puts_emotion_lines_in_neutral(tmp_path, monkeypatch):
    main = tmp_path / "a"
    _touch(main / "H001.wav")
    assert _run(tmp_path, monkeypatch, str(main), "--model", "m", "--neutral-only") == 0
    assert (tmp_path / "Data" / "m" / "raw" / "H001.wav").exists()
    assert not (tmp_path / "Data" / "m" / "raw" / "Happy").exists()


def test_neutral_is_not_a_subfolder(tmp_path, monkeypatch):
    # Style-Bert-VITS2 names the mean of everything "Neutral"; a raw/Neutral/ folder would make a second one
    main = tmp_path / "a"
    _touch(main / "N001.wav")
    _touch(main / "H001.wav")
    assert _run(tmp_path, monkeypatch, str(main), "--model", "m") == 0
    assert not (tmp_path / "Data" / "m" / "raw" / "Neutral").exists()
    assert (tmp_path / "Data" / "m" / "raw" / "N001.wav").exists()
    assert (tmp_path / "Data" / "m" / "raw" / "Happy" / "H001.wav").exists()


def test_long_readings_stay_out_unless_asked(tmp_path, monkeypatch):
    main = tmp_path / "a"
    _touch(main / "L001.wav")
    _touch(main / "N001.wav")
    assert _run(tmp_path, monkeypatch, str(main), "--model", "m") == 0
    esd = (tmp_path / "Data" / "m" / "esd.list").read_text(encoding="utf-8")
    assert "N001" in esd and "L001" not in esd


def test_unknown_style_in_extra_is_refused(tmp_path, monkeypatch):
    main = tmp_path / "a"
    _touch(main / "N001.wav")
    assert _run(tmp_path, monkeypatch, str(main), "--model", "m", "--extra", f"Joy={main}") == 1
