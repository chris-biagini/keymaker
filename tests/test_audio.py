"""CoachAudio degraded path: no CircuitPython audio libs on the host."""
import sys
from pathlib import Path

# firmware/ is deliberately NOT in pytest.ini's pythonpath: firmware/code.py
# would shadow the stdlib `code` module suite-wide. Append (never insert) so
# the stdlib always wins and only `pad.*` resolves from firmware/.
sys.path.append(str(Path(__file__).resolve().parent.parent / "firmware"))

from pad.audio import CoachAudio


class FakePad:
    pass                                    # no _speaker_enable attribute


def test_degrades_to_noop_without_audio_libs():
    a = CoachAudio(FakePad())
    a.enable()                              # none of these may raise
    a.play("kick")
    a.play("nonsense")
    a.disable()
