"""Coach curriculum and scoring. Pure: no CircuitPython imports, no stdlib
modules missing from CircuitPython (no statistics, no dataclasses)."""

GREEN_MS = 35
MISS_MS = 120
VAR_DIV_MS = 40.0
LATE_GATE_MS = 10.0
UNLOCK_MEAN = 0.85
UNLOCK_SESSIONS = 3
BPM_MIN, BPM_MAX, BPM_STEP, BPM_DEFAULT = 60, 140, 5, 95
SWING_MIN, SWING_MAX = 50, 67
LOOPS = 8

KICK, SNARE, HAT = "kick", "snare", "hat"

# Okabe-Ito, CVD-safe (spec section 9)
COL_GREEN = 0x009E73
COL_AMBER = 0xE69F00
COL_RED = 0xD55E00
COL_ACCENT = 0xF0E442

_K = (0, 8, 16, 24)
_S = (4, 12, 20, 28)
_H = tuple(range(0, 32, 2))

STAGES = (
    {"name": "metronome", "pattern": {}, "swing": False, "variance": False},
    {"name": "on the one", "pattern": {KICK: (0, 16)}, "swing": False, "variance": False},
    {"name": "backbeat", "pattern": {KICK: _K, SNARE: _S}, "swing": False, "variance": False},
    {"name": "the pocket", "pattern": {KICK: _K, SNARE: _S, HAT: _H}, "swing": False, "variance": False},
    {"name": "swing", "pattern": {KICK: _K, SNARE: _S, HAT: _H}, "swing": True, "variance": False},
    {"name": "off the grid", "pattern": {KICK: _K, SNARE: _S}, "swing": False, "variance": True},
)


def loop_grid_ms(stage_idx, bpm, swing=50):
    """Expected hits for one two-bar loop: sorted [(instr, ms offset)]."""
    st = STAGES[stage_idx]
    six = 15000.0 / bpm            # sixteenth note in ms
    out = []
    for instr, slots in st["pattern"].items():
        for s in slots:
            t = s * six
            if st["swing"] and instr == HAT and s % 4 == 2:
                t = (s - 2) * six + (swing / 100.0) * (4.0 * six)
            out.append((instr, t))
    out.sort(key=lambda p: (p[1], p[0]))
    return out
