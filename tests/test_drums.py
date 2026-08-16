"""The committed WAVs must match what tools/make_drums.py generates."""
import importlib.util
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOUNDS = ROOT / "firmware" / "sounds"
NAMES = ("kick", "snare", "hat", "click_hi", "click_lo")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "make_drums", ROOT / "tools" / "make_drums.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wavs_exist_with_correct_format():
    for name in NAMES:
        with wave.open(str(SOUNDS / (name + ".wav")), "rb") as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == 22050
            assert w.getnframes() > 200


def test_generation_is_deterministic_and_committed(tmp_path):
    mod = _load_tool()
    mod.OUT = tmp_path
    mod.main()
    for name in NAMES:
        fresh = (tmp_path / (name + ".wav")).read_bytes()
        committed = (SOUNDS / (name + ".wav")).read_bytes()
        assert fresh == committed, name + " drifted from its generator"
