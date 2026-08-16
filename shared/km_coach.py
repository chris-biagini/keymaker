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
# Verdict colors, chosen at the bench 2026-08-16: the load-bearing
# distinction (in the pocket vs not) sits on the blue axis, which survives
# every CVD type — bluish-green vs orange did not survive a 150 ms NeoPixel
# flash. All values still Okabe-Ito.
COL_GOOD = 0x56B4E9    # sky blue — on time
COL_LATE = 0xE69F00    # orange — dragging
COL_EARLY = 0xCC79A7   # reddish purple — rushing
COL_MISS = 0xD55E00    # vermillion — miss / stray
COL_ACCENT = 0xF0E442  # yellow — downbeat flash

_K = (0, 8, 16, 24)
_S = (4, 12, 20, 28)
_H = tuple(range(0, 32, 2))

STAGES = (
    {"name": "metronome", "hint": "free play",
     "pattern": {}, "swing": False, "variance": False},
    {"name": "on the one", "hint": "kick on the 1",
     "pattern": {KICK: (0, 16)}, "swing": False, "variance": False},
    {"name": "backbeat", "hint": "kick 1+3 snare 2+4",
     "pattern": {KICK: _K, SNARE: _S}, "swing": False, "variance": False},
    {"name": "the pocket", "hint": "+ hats on 8ths",
     "pattern": {KICK: _K, SNARE: _S, HAT: _H}, "swing": False, "variance": False},
    {"name": "swing", "hint": "dial swings the hats",
     "pattern": {KICK: _K, SNARE: _S, HAT: _H}, "swing": True, "variance": False},
    {"name": "off the grid", "hint": "drag the snare late",
     "pattern": {KICK: _K, SNARE: _S}, "swing": False, "variance": True},
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


def _pstdev(xs):
    m = sum(xs) / len(xs)
    return (sum((x - m) * (x - m) for x in xs) / len(xs)) ** 0.5


class SessionScorer:
    """Incremental hit matcher/scorer; times are float ms since epoch.

    add_expected must be called in nondecreasing time order — both scans
    below rely on it to stay O(window) on the RP2040.
    """

    def __init__(self, variance=False):
        self.variance = variance
        self.pending = []          # [instr, t_ms, resolved]
        self.counts = {"greens": 0, "ambers": 0, "reds": 0,
                       "misses": 0, "strays": 0}
        self.offsets = []          # variance-mode snare offsets
        self.grid_greens = 0
        self.grid_expected = 0
        self.grid_resolved = 0
        self.snare_expected = 0
        self._idx = 0              # first entry that may still be open

    def _grid(self, instr):
        return not (self.variance and instr == SNARE)

    def add_expected(self, instr, t_ms):
        self.pending.append([instr, t_ms, False])
        if self._grid(instr):
            self.grid_expected += 1
        else:
            self.snare_expected += 1

    def on_hit(self, instr, t_ms):
        best = None
        best_off = 0.0
        for k in range(self._idx, len(self.pending)):
            e = self.pending[k]
            if e[1] > t_ms + MISS_MS:
                break
            if e[2] or e[0] != instr:
                continue
            off = t_ms - e[1]
            if off < -MISS_MS or off > MISS_MS:
                continue
            if best is None or abs(off) < abs(best_off):
                best, best_off = e, off
        if best is None:
            self.counts["strays"] += 1
            return "stray"
        best[2] = True
        if not self._grid(instr):
            self.offsets.append(best_off)
            self.counts["greens"] += 1
            return "green"
        self.grid_resolved += 1
        if abs(best_off) <= GREEN_MS:
            self.counts["greens"] += 1
            self.grid_greens += 1
            return "green"
        if best_off < 0:
            self.counts["reds"] += 1
            return "red"
        self.counts["ambers"] += 1
        return "amber"

    def expire(self, now_ms):
        missed = []
        while self._idx < len(self.pending):
            e = self.pending[self._idx]
            if now_ms - e[1] <= MISS_MS:
                break
            if not e[2]:
                e[2] = True
                self.counts["misses"] += 1
                if self._grid(e[0]):
                    self.grid_resolved += 1
                missed.append((e[0], e[1]))
            self._idx += 1
        return missed

    def live_accuracy(self):
        denom = self.grid_resolved + self.counts["strays"]
        if denom == 0:
            return None
        return self.grid_greens / denom

    def finalize(self):
        self.expire(1e12)
        out = dict(self.counts)
        denom = self.grid_expected + self.counts["strays"]
        acc = self.grid_greens / denom if denom else 0.0
        out["accuracy"] = acc
        if not self.variance:
            out["score"] = acc
            return out
        n = len(self.offsets)
        mean = sum(self.offsets) / n if n else 0.0
        var_score = 0.0
        if n and mean >= LATE_GATE_MS:
            var_score = max(0.0, 1.0 - _pstdev(self.offsets) / VAR_DIV_MS)
            if self.snare_expected:
                var_score *= float(n) / self.snare_expected
        out["mean_offset"] = mean
        out["score"] = min(acc, var_score)
        return out


def _passed(scores):
    for i in range(len(scores) - UNLOCK_SESSIONS + 1):
        w = scores[i:i + UNLOCK_SESSIONS]
        if sum(w) / UNLOCK_SESSIONS >= UNLOCK_MEAN:
            return True
    return False


def summarize(history):
    """Derive all progression state from raw history (never stored)."""
    by = {}
    practice = 0
    for h in history:
        by.setdefault(int(h.get("stage", 0)), []).append(float(h.get("score") or 0.0))
        practice += int(h.get("duration_ms") or 0)
    unlocked = 1
    for s in range(1, 5):
        if _passed(by.get(s, [])):
            unlocked = s + 1
        else:
            break
    stages = {}
    for s in by:
        if s == 0:
            continue
        scores = by[s]
        stages[str(s)] = {"best": max(scores), "recent": scores[-UNLOCK_SESSIONS:]}
    return {"unlocked": unlocked, "graduated": _passed(by.get(5, [])),
            "stages": stages, "practice_ms": practice}


def merge_unlock(host_state, local_sessions):
    """Host snapshot + this-power-cycle sessions -> (unlocked, graduated)."""
    by = {}
    for key in (host_state.get("stages") or {}):
        info = host_state["stages"][key]
        by[int(key)] = list(info.get("recent") or [])
    for sess in local_sessions:
        by.setdefault(int(sess.get("stage", 0)), []).append(float(sess.get("score") or 0.0))
    unlocked = min(5, max(1, int(host_state.get("unlocked") or 1)))
    for s in range(unlocked, 5):
        if _passed(by.get(s, [])):
            unlocked = s + 1
        else:
            break
    graduated = bool(host_state.get("graduated")) or _passed(by.get(5, []))
    return unlocked, graduated


def prune_acked(local_sessions, acked_sessions):
    """Drop sessions a host ack already reflects (list equality, each once)."""
    out = list(local_sessions)
    for sess in acked_sessions:
        try:
            out.remove(sess)
        except ValueError:
            pass
    return out


def format_results(counts):
    return ("g" + str(counts["greens"]) + " a" + str(counts["ambers"])
            + " r" + str(counts["reds"]) + " m" + str(counts["misses"])
            + " s" + str(counts["strays"]))
