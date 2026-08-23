# OLED Weather Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the OLED's placeholder text layout with the three-state
weather display: digital rain when calm, a recency-sized bell wall when
workspaces are ringing, a marquee wipe on workspace switch, and a persistent
REC/submap badge overlay.

**Architecture:** All decidable logic (state machine, recency stamps, wall
layout, marquee position, rain advancement) lives in a new pure-Python
`shared/km_weather.py`, host-tested with pytest. `firmware/pad/ui.py` is
rewritten as a thin displayio edge: a layered scene graph (rain / bell wall /
marquee / badges as sibling Groups toggled via `.hidden`), digit bitmaps built
at boot from a committed generated asset, rain glyphs built at boot from
`terminalio.FONT`. Cockpit shrinks to bookkeeping and event forwarding. No
daemon changes.

**Tech Stack:** CircuitPython 10.x (`displayio`, `terminalio`,
`adafruit_display_text`, `adafruit_ticks`), host-side pytest (`pytest.ini`:
`pythonpath = shared daemon`).

**Spec:** `docs/specs/2026-08-23-oled-weather-design.md`

## Global Constraints

- 1-bit 128×64 SH1106 OLED; every pixel on or off.
- pad-timing §5: cache what was last written; touch hardware only on change;
  never `fill(0)` a live bitmap; hidden/static layers must cost zero writes.
- pad-timing §3: all timing epoch-anchored via `ticks_add`/`ticks_diff`;
  never compare raw `ticks_ms` values.
- `shared/` modules must run on both CPython 3.11+ and CircuitPython: no
  `typing`, no `dataclasses`, no f-string `=`, no `random.Random` (CircuitPython's
  `random` has module-level functions only — inject the rng).
- No changes under `daemon/` and no protocol changes.
- Deploy only via `system/deploy-firmware.sh` (autoreload guard must stay).
- Commit after every task; prefixes per repo convention (`feat:`, `docs:`).

## File Structure

- `shared/km_weather.py` — new; pure logic (Tasks 1–4). Deploys to
  CIRCUITPY `lib/` automatically via the deploy script's `km_*.py` filter.
- `tools/gen_digits.py` — new; deterministic digit-asset generator (Task 5).
- `firmware/assets/__init__.py`, `firmware/assets/digits.py` — new; generated
  digit bitmasks (Task 5). Rides along in the deploy rsync of `firmware/`.
- `firmware/pad/ui.py` — rewritten (Task 6).
- `firmware/apps/cockpit.py` — modified (Task 7).
- `tests/test_weather.py`, `tests/test_digit_assets.py` — new.
- `README.md`, `docs/pad-timing.md` — untouched except README's OLED bullet
  (Task 8).

---

### Task 1: km_weather — state machine, stamps, bell ordering

**Files:**
- Create: `shared/km_weather.py`
- Test: `tests/test_weather.py`

**Interfaces:**
- Produces: `weather(urgent, link_up) -> str` ("calm" | "ringing" | "nolink");
  `update_stamps(stamps, urgent, now) -> None` (mutates `stamps` dict
  `{ws:int -> tick_ms:int}`); `bell_order(stamps, diff) -> list[int]`
  (newest first; `diff` is a `ticks_diff`-shaped callable). Tasks 2–7 build
  on these names exactly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_weather.py
from km_weather import weather, update_stamps, bell_order


def plain_diff(a, b):
    return a - b


def test_weather_states():
    assert weather([], link_up=True) == "calm"
    assert weather([3], link_up=True) == "ringing"
    assert weather([3, 5], link_up=True) == "ringing"
    assert weather([], link_up=False) == "nolink"
    # link state dominates: ringing info is stale without a link
    assert weather([3], link_up=False) == "nolink"


def test_update_stamps_stamps_new_bells_once():
    stamps = {}
    update_stamps(stamps, [3], now=100)
    assert stamps == {3: 100}
    update_stamps(stamps, [3], now=200)   # already ringing: stamp unchanged
    assert stamps == {3: 100}
    update_stamps(stamps, [3, 5], now=300)
    assert stamps == {3: 100, 5: 300}


def test_update_stamps_clears_silenced_bells():
    stamps = {3: 100, 5: 300}
    update_stamps(stamps, [5], now=400)
    assert stamps == {5: 300}
    update_stamps(stamps, [], now=500)
    assert stamps == {}


def test_bell_order_newest_first():
    assert bell_order({3: 100, 5: 300, 1: 200}, plain_diff) == [5, 1, 3]
    assert bell_order({}, plain_diff) == []
    assert bell_order({4: 700}, plain_diff) == [4]


def test_bell_order_tie_breaks_deterministically():
    # daemon snapshot after reconnect stamps several bells the same tick;
    # lower workspace first among equals so the wall is stable across calls
    assert bell_order({6: 100, 2: 100, 4: 100}, plain_diff) == [2, 4, 6]


def test_bell_order_survives_tick_wraparound():
    # adafruit_ticks wraps at 2**29; ticks_diff-style compare must still
    # order a stamp taken just before the wrap behind one taken just after
    period = 2 ** 29

    def wrap_diff(a, b):
        return ((a - b + period // 2) % period) - period // 2

    stamps = {1: period - 10, 2: 5}      # ws 2 stamped 15 ticks AFTER ws 1
    assert bell_order(stamps, wrap_diff) == [2, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'km_weather'`

- [ ] **Step 3: Write the implementation**

```python
# shared/km_weather.py
"""OLED weather: pure state and layout logic. No displayio; host-testable.

Runs on both CPython (tests) and CircuitPython (firmware), so: no typing, no
dataclasses, and rngs/tick-diff functions are injected rather than imported.
"""


def weather(urgent, link_up):
    """The screen's base state. Link loss dominates: bells are stale then."""
    if not link_up:
        return "nolink"
    return "ringing" if urgent else "calm"


def update_stamps(stamps, urgent, now):
    """Reconcile recency stamps {ws: tick_ms} with the urgent list.

    A workspace is stamped the first tick its bell is seen and the stamp is
    left alone while it keeps ringing -- recency means "when it started",
    not "last time the daemon repeated it". Cleared bells drop their stamp.
    """
    for ws in urgent:
        if ws not in stamps:
            stamps[ws] = now
    for ws in list(stamps):
        if ws not in urgent:
            del stamps[ws]


def bell_order(stamps, diff):
    """Ringing workspaces newest-first; ties break low-workspace-first.

    `diff` is ticks_diff (injected): stamps are ticks_ms values, so ordering
    must go through wraparound-safe subtraction against a common reference,
    never a raw comparison (docs/pad-timing.md section 3). All live stamps
    are within one session of each other, far inside ticks_diff's half-range.
    """
    if not stamps:
        return []
    ref = next(iter(stamps.values()))
    return sorted(stamps, key=lambda ws: (-diff(stamps[ws], ref), ws))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /home/chris/src/keymaker
git add shared/km_weather.py tests/test_weather.py
git commit -m "feat: km_weather state machine, bell stamps, recency ordering"
```

---

### Task 2: km_weather — bell wall layout

**Files:**
- Modify: `shared/km_weather.py` (append)
- Test: `tests/test_weather.py` (append)

**Interfaces:**
- Consumes: `bell_order` output (list of ws ints, newest first).
- Produces: constants `SCREEN_W = 128`, `SCREEN_H = 64`, `BIG_W = 28`,
  `BIG_H = 48`, `SMALL_W = 14`, `SMALL_H = 24`; function
  `wall_layout(order) -> list[tuple]` of `(ws, size, x, y)` with `size` in
  `("big", "small")`. Task 6 renders exactly these tuples.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_weather.py
from km_weather import (SCREEN_W, SCREEN_H, BIG_W, BIG_H, SMALL_W, SMALL_H,
                        wall_layout)


def test_wall_layout_single_bell_is_one_big_numeral():
    assert wall_layout([3]) == [(3, "big", 4, 8)]


def test_wall_layout_two_bells():
    assert wall_layout([5, 2]) == [(5, "big", 4, 8), (2, "small", 40, 20)]


def test_wall_layout_six_bells_all_fit_on_screen():
    placed = wall_layout([6, 5, 4, 3, 2, 1])
    assert placed[0] == (6, "big", 4, 8)
    assert [p[0] for p in placed[1:]] == [5, 4, 3, 2, 1]
    assert [p[1] for p in placed[1:]] == ["small"] * 5
    for ws, size, x, y in placed:
        w = BIG_W if size == "big" else SMALL_W
        h = BIG_H if size == "big" else SMALL_H
        assert 0 <= x and x + w <= SCREEN_W
        assert 0 <= y and y + h <= SCREEN_H


def test_wall_layout_smalls_are_evenly_spaced():
    placed = wall_layout([1, 2, 3, 4])
    xs = [p[2] for p in placed[1:]]
    assert xs == [40, 58, 76]            # SMALL_W + 4 gap


def test_wall_layout_empty():
    assert wall_layout([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: new tests FAIL with ImportError (`wall_layout`)

- [ ] **Step 3: Write the implementation**

```python
# append to shared/km_weather.py

SCREEN_W = 128
SCREEN_H = 64
BIG_W, BIG_H = 28, 48        # 2x nearest-neighbor upscale of the small digit
SMALL_W, SMALL_H = 14, 24
_SMALL_GAP = 4
_SMALL_X0 = 40               # first small sits right of the big numeral
_BIG_XY = (4, 8)             # (128-28 leaves right field; 64-48 centers at 8)
_SMALL_Y = 20                # (64-24)//2


def wall_layout(order):
    """Place bell-wall numerals: newest big on the left, the rest in a row.

    Five smalls at pitch SMALL_W+4 starting at x=40 end at x=126 -- the
    worst case (all six workspaces ringing) fits 128px exactly. A vertical
    stack would not fit: two 24px numerals already exceed nothing, but five
    do not fit 64px.
    """
    if not order:
        return []
    out = [(order[0], "big", _BIG_XY[0], _BIG_XY[1])]
    x = _SMALL_X0
    for ws in order[1:6]:
        out.append((ws, "small", x, _SMALL_Y))
        x += SMALL_W + _SMALL_GAP
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
cd /home/chris/src/keymaker
git add shared/km_weather.py tests/test_weather.py
git commit -m "feat: bell wall layout math"
```

---

### Task 3: km_weather — marquee position

**Files:**
- Modify: `shared/km_weather.py` (append)
- Test: `tests/test_weather.py` (append)

**Interfaces:**
- Produces: `MARQUEE_MS = 600`; `marquee_x(elapsed_ms) -> int | None` —
  x of the big numeral's left edge, or None when the wipe is over (or not
  started, elapsed < 0). Task 6 hides the marquee layer on None.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_weather.py
from km_weather import MARQUEE_MS, marquee_x


def test_marquee_enters_from_right_edge():
    assert marquee_x(0) == SCREEN_W


def test_marquee_moves_monotonically_left():
    xs = [marquee_x(t) for t in range(0, MARQUEE_MS, 50)]
    assert all(b <= a for a, b in zip(xs, xs[1:]))


def test_marquee_fully_exits_left_by_the_end():
    # last in-flight position: the numeral is at most partially on screen
    assert marquee_x(MARQUEE_MS - 1) <= 0


def test_marquee_over_and_not_started_return_none():
    assert marquee_x(MARQUEE_MS) is None
    assert marquee_x(MARQUEE_MS + 5000) is None
    assert marquee_x(-1) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: new tests FAIL with ImportError (`marquee_x`)

- [ ] **Step 3: Write the implementation**

```python
# append to shared/km_weather.py

MARQUEE_MS = 600


def marquee_x(elapsed_ms):
    """Left edge of the wipe numeral, traversing right edge to fully off
    left in MARQUEE_MS. None = not in flight (layer should hide). Pure
    function of elapsed time so a slow tick skips ahead instead of lagging
    (docs/pad-timing.md section 3)."""
    if elapsed_ms < 0 or elapsed_ms >= MARQUEE_MS:
        return None
    span = SCREEN_W + BIG_W
    return SCREEN_W - (span * elapsed_ms) // MARQUEE_MS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
cd /home/chris/src/keymaker
git add shared/km_weather.py tests/test_weather.py
git commit -m "feat: marquee wipe position function"
```

---

### Task 4: km_weather — RainField and FrameClock

**Files:**
- Modify: `shared/km_weather.py` (append)
- Test: `tests/test_weather.py` (append)

**Interfaces:**
- Produces: `class RainField` with `__init__(self, rng, cols, rows,
  glyphs=16, max_drops=10)` and `step() -> list[tuple]` of
  `(col, row, kind, glyph_i)` where `kind` is `"head" | "dim" | "off"`.
  `rng` is anything with `.random()` and `.randrange(n)` — the firmware
  passes CircuitPython's `random` **module** (it has no `Random` class);
  tests pass `random.Random(seed)`. Task 6 maps kinds to tile-sheet banks.
- Produces: `class FrameClock` with `__init__(self, period, now, add, diff)`
  (`add`/`diff` are `ticks_add`/`ticks_diff`-shaped callables) and
  `advance(now) -> int` — how many whole periods have elapsed since the last
  call (0 = not due). Deadlines sit at `epoch + n*period`: advancing the next
  deadline by exact `add(prev, period)` increments never drifts, and the
  returned count lets callers account for frames a slow tick skipped. Task 6
  drives all animation from one FrameClock.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_weather.py
import random

from km_weather import RainField


def collect(field, steps):
    return [field.step() for _ in range(steps)]


def test_rain_is_deterministic_for_a_fixed_seed():
    a = collect(RainField(random.Random(7), cols=21, rows=8), 50)
    b = collect(RainField(random.Random(7), cols=21, rows=8), 50)
    assert a == b


def test_rain_stays_in_bounds_and_kinds_are_valid():
    field = RainField(random.Random(3), cols=21, rows=8, glyphs=16)
    for frame in collect(field, 300):
        for col, row, kind, glyph_i in frame:
            assert 0 <= col < 21
            assert 0 <= row < 8
            assert kind in ("head", "dim", "off")
            assert 0 <= glyph_i < 16


def test_rain_cells_change_state_never_teleport():
    # A cell may only become "head" from off, "dim" from head, "off" from
    # dim -- the frame deltas are the whole contract, so a violation here
    # is a visible artifact on hardware.
    field = RainField(random.Random(11), cols=21, rows=8)
    state = {}
    for frame in collect(field, 300):
        for col, row, kind, _ in frame:
            prev = state.get((col, row), "off")
            allowed = {"off": ("head",), "head": ("dim",), "dim": ("off",)}
            assert kind in allowed[prev], (prev, kind)
            state[(col, row)] = kind


def test_rain_eventually_rains_and_eventually_clears_cells():
    field = RainField(random.Random(5), cols=21, rows=8)
    kinds = [k for frame in collect(field, 300) for (_, _, k, _) in frame]
    assert "head" in kinds
    assert "dim" in kinds
    assert "off" in kinds


def test_rain_respects_max_drops():
    field = RainField(random.Random(1), cols=21, rows=8, max_drops=3)
    for _ in range(300):
        field.step()
        assert len(field.drops) <= 3


def test_frameclock_not_due_returns_zero():
    from km_weather import FrameClock
    clock = FrameClock(100, now=1000, add=lambda a, b: a + b,
                       diff=lambda a, b: a - b)
    assert clock.advance(1050) == 0
    assert clock.advance(1099) == 0


def test_frameclock_counts_skipped_frames_and_does_not_drift():
    from km_weather import FrameClock
    add, diff = (lambda a, b: a + b), (lambda a, b: a - b)
    clock = FrameClock(100, now=1000, add=add, diff=diff)
    assert clock.advance(1100) == 1
    assert clock.advance(1550) == 4       # a stall: frames counted, not lost
    # deadlines stay on the epoch grid: next due at exactly 1600
    assert clock.advance(1599) == 0
    assert clock.advance(1600) == 1


def test_frameclock_wraparound_safe():
    from km_weather import FrameClock
    period = 2 ** 29

    def wdiff(a, b):
        return ((a - b + period // 2) % period) - period // 2

    def wadd(a, b):
        return (a + b) % period

    clock = FrameClock(100, now=period - 50, add=wadd, diff=wdiff)
    assert clock.advance(period - 10) == 0
    assert clock.advance(60) == 1         # wrapped past the deadline at 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: new tests FAIL with ImportError (`RainField`)

- [ ] **Step 3: Write the implementation**

```python
# append to shared/km_weather.py

_SPAWN_P = 0.35              # per-step chance of trying to start a new drop


class RainField:
    """Sparse matrix-rain over a cols x rows glyph grid, as pure data.

    step() returns only the cells that changed this frame -- the drawing
    edge applies them 1:1 as tile writes, so the delta list IS the
    hardware-touch budget (docs/pad-timing.md section 5). At most one drop
    per column; a drop is a head glyph descending with a trail of "dim"
    glyphs behind it, erased from the tail.
    """

    def __init__(self, rng, cols, rows, glyphs=16, max_drops=10):
        self.rng = rng
        self.cols = cols
        self.rows = rows
        self.glyphs = glyphs
        self.max_drops = max_drops
        self.drops = {}          # col -> {"head": row, "len": n, "glyph": i}

    def step(self):
        changes = []
        for col in list(self.drops):
            d = self.drops[col]
            if 0 <= d["head"] < self.rows:
                changes.append((col, d["head"], "dim", d["glyph"]))
            d["head"] += 1
            tail = d["head"] - d["len"]          # first row still lit
            if 0 <= tail - 1 < self.rows:
                changes.append((col, tail - 1, "off", 0))
            if d["head"] < self.rows:
                d["glyph"] = self.rng.randrange(self.glyphs)
                changes.append((col, d["head"], "head", d["glyph"]))
            if tail >= self.rows:
                del self.drops[col]
        if len(self.drops) < self.max_drops and self.rng.random() < _SPAWN_P:
            col = self.rng.randrange(self.cols)
            if col not in self.drops:
                glyph = self.rng.randrange(self.glyphs)
                self.drops[col] = {"head": 0,
                                   "len": 3 + self.rng.randrange(3),
                                   "glyph": glyph}
                changes.append((col, 0, "head", glyph))
        return changes


class FrameClock:
    """Fixed-rate animation scheduler, epoch-anchored and wraparound-safe.

    Deadlines sit on the grid epoch + n*period: each deadline is advanced by
    an exact add(prev, period), which is the same grid with no accumulated
    drift (docs/pad-timing.md section 3 forbids chaining from *event
    occurrence* times, not constant-period deadline advancement). advance()
    returns how many whole periods elapsed, so a caller can account for
    frames a slow tick skipped instead of silently losing them.
    """

    def __init__(self, period, now, add, diff):
        self.period = period
        self.add = add
        self.diff = diff
        self.next_at = add(now, period)

    def advance(self, now):
        n = 0
        while self.diff(now, self.next_at) >= 0:
            self.next_at = self.add(self.next_at, self.period)
            n += 1
        return n
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_weather.py -v`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
cd /home/chris/src/keymaker
git add shared/km_weather.py tests/test_weather.py
git commit -m "feat: RainField delta rain simulation + FrameClock scheduler"
```

---

### Task 5: digit assets — generator, committed module, byte-match test

**Files:**
- Create: `tools/gen_digits.py`
- Create: `firmware/assets/__init__.py` (empty), `firmware/assets/digits.py`
  (generated)
- Test: `tests/test_digit_assets.py`

**Interfaces:**
- Produces: `firmware/assets/digits.py` defining
  `DIGITS: tuple[tuple[int, ...], ...]` — ten digits (0–9), each a tuple of
  24 row bitmasks; bit `i` set = pixel at column `i` (of 14) lit. Task 6
  builds `displayio.Bitmap`s from these masks (small = 14×24 direct, big =
  28×48 by 2× nearest-neighbor).

Digits are chunky seven-segment geometry rendered onto the 14×24 grid —
deterministic from pure integer math, no font files, no image libraries, so
the generator produces identical bytes on every machine (the Coach WAV
pattern: committed asset + test asserting it matches the generator).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digit_assets.py
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_committed_digits_match_generator():
    out = subprocess.run(
        [sys.executable, str(REPO / "tools" / "gen_digits.py"), "--stdout"],
        capture_output=True, text=True, check=True)
    committed = (REPO / "firmware" / "assets" / "digits.py").read_text()
    assert out.stdout == committed


def test_digit_shapes():
    sys.path.insert(0, str(REPO / "firmware"))
    try:
        from assets.digits import DIGITS
    finally:
        sys.path.pop(0)
    assert len(DIGITS) == 10
    for rows in DIGITS:
        assert len(rows) == 24
        for mask in rows:
            assert 0 <= mask < (1 << 14)
    # every digit must actually mark pixels, and 8 lights all 7 segments
    assert all(any(rows) for rows in DIGITS)
    assert sum(bin(m).count("1") for m in DIGITS[8]) > \
        sum(bin(m).count("1") for m in DIGITS[1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/src/keymaker && python -m pytest tests/test_digit_assets.py -v`
Expected: FAIL — `tools/gen_digits.py` does not exist

- [ ] **Step 3: Write the generator**

```python
#!/usr/bin/env python3
# tools/gen_digits.py
"""Generate firmware/assets/digits.py: 14x24 seven-segment digit bitmasks.

Deterministic pure-integer rendering -- no fonts, no PIL -- so the committed
module byte-matches this generator on any machine (tests/test_digit_assets.py
enforces it). Run with --stdout to print instead of writing the file.
"""
import sys
from pathlib import Path

W, H, T = 14, 24, 3          # grid width, height, stroke thickness

# (row0, row1, col0, col1) half-open pixel rectangles
SEGMENTS = {
    "A": (0, T, 0, W),                     # top bar
    "G": ((H - T) // 2, (H - T) // 2 + T, 0, W),   # middle bar
    "D": (H - T, H, 0, W),                 # bottom bar
    "F": (0, H // 2, 0, T),                # top-left
    "B": (0, H // 2, W - T, W),            # top-right
    "E": (H // 2, H, 0, T),                # bottom-left
    "C": (H // 2, H, W - T, W),            # bottom-right
}

DIGIT_SEGS = {
    0: "ABCDEF", 1: "BC", 2: "ABGED", 3: "ABGCD", 4: "FGBC",
    5: "AFGCD", 6: "AFGECD", 7: "ABC", 8: "ABCDEFG", 9: "ABCDFG",
}


def render(digit):
    rows = [0] * H
    for seg in DIGIT_SEGS[digit]:
        r0, r1, c0, c1 = SEGMENTS[seg]
        for r in range(r0, r1):
            for c in range(c0, c1):
                rows[r] |= 1 << c
    return tuple(rows)


def module_text():
    lines = ['"""Generated by tools/gen_digits.py -- do not edit.',
             "",
             "Ten digits 0-9 as 24 row bitmasks each; bit i = column i of 14.",
             '"""',
             "DIGITS = ("]
    for d in range(10):
        rows = render(d)
        lines.append("    (  # %d" % d)
        for i in range(0, len(rows), 6):
            chunk = ", ".join("0x%04x" % m for m in rows[i:i + 6])
            lines.append("        " + chunk + ",")
        lines.append("    ),")
    lines.append(")")
    return "\n".join(lines) + "\n"


def main():
    text = module_text()
    if "--stdout" in sys.argv:
        sys.stdout.write(text)
        return
    out = Path(__file__).resolve().parent.parent / "firmware" / "assets"
    out.mkdir(exist_ok=True)
    (out / "__init__.py").touch()
    (out / "digits.py").write_text(text)
    print("wrote", out / "digits.py")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate the asset and run the tests**

```bash
cd /home/chris/src/keymaker
python tools/gen_digits.py
python -m pytest tests/test_digit_assets.py -v
```
Expected: `wrote .../firmware/assets/digits.py`, then 2 passed

- [ ] **Step 5: Eyeball the digits**

```bash
cd /home/chris/src/keymaker
python - <<'PY'
import sys
sys.path.insert(0, "firmware")
from assets.digits import DIGITS
for rows in DIGITS:
    print("\n".join("".join("#" if m >> c & 1 else "." for c in range(14))
                    for m in rows))
    print()
PY
```
Expected: ten recognizable chunky digits. If any reads wrong, fix
`SEGMENTS`/`DIGIT_SEGS` in the generator, re-run it, re-run the tests.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/src/keymaker
git add tools/gen_digits.py firmware/assets/ tests/test_digit_assets.py
git commit -m "feat: generated seven-segment digit assets + byte-match test"
```

---

### Task 6: rewrite firmware/pad/ui.py — the layered Screen

**Files:**
- Modify: `firmware/pad/ui.py` (full rewrite)

**Interfaces:**
- Consumes: everything Tasks 1–5 produced: `km_weather.wall_layout`,
  `marquee_x`, `MARQUEE_MS`, `RainField`, `SCREEN_W/H`, `BIG_W/H`,
  `SMALL_W/H`, `assets.digits.DIGITS`.
- Produces (Cockpit calls these in Task 7):
  `Screen(display)`; `set_weather(state)` with state
  `"calm" | "ringing" | "nolink"`; `set_bells(order)` (list from
  `bell_order`); `marquee(ws)`; `set_flags(rec, submap)` (bool, str);
  `tick(now)`. The old `set_header`/`set_focus`/`idle_card`/`WIDTH_CHARS`
  are deleted — Task 7 removes their callers in the same PR-sized arc.

No host test (displayio is firmware-only); correctness leans on Tasks 1–5
covering every decision and this file staying mechanical. Hardware bench
verification is Task 8.

- [ ] **Step 1: Write the new ui.py**

```python
# firmware/pad/ui.py  (full replacement)
"""OLED weather: layered scene graph -- rain / bell wall / marquee / badges.

All decisions live in km_weather (host-tested); this file only translates
its outputs into displayio mutations. Discipline per docs/pad-timing.md
section 5: every write path diffs against what was last written; hidden or
static layers cost zero work; no live bitmap is ever cleared wholesale.
"""
import random

import displayio
import terminalio
from adafruit_display_text import label
from adafruit_ticks import ticks_add, ticks_diff, ticks_ms

import km_weather
from assets.digits import DIGITS

FRAME_MS = 100               # ~10 fps animation clock
RAIN_DIV = 2                 # rain advances every 2nd frame (~5 fps)
RAIN_GLYPHS = "01<>=+*:#$KMTXZ7"          # 16 glyphs, matrix-flavored

_KIND_BANK = {"head": 0, "dim": 1}        # tile-sheet banks; "off" = blank


def _mono_palette():
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = 0xFFFFFF
    pal.make_transparent(0)   # layers composite over each other
    return pal


def _digit_bitmap(rows, scale):
    """Build a 1-bit Bitmap from 14-wide row masks, nearest-neighbor scaled."""
    w, h = km_weather.SMALL_W * scale, km_weather.SMALL_H * scale
    bmp = displayio.Bitmap(w, h, 2)
    for r, mask in enumerate(rows):
        for c in range(km_weather.SMALL_W):
            if mask >> c & 1:
                for dr in range(scale):
                    for dc in range(scale):
                        bmp[c * scale + dc, r * scale + dr] = 1
    return bmp


def _rain_sheet(font):
    """Tile sheet from terminalio glyphs: bank 0 bright, bank 1 dimmed by a
    checkerboard mask (the 1-bit stand-in for 50% gray). Built once at boot."""
    gw, gh = font.get_bounding_box()[0], font.get_bounding_box()[1]
    n = len(RAIN_GLYPHS)
    # +1 leading blank tile (index 0) so "off" is a plain tile write too
    sheet = displayio.Bitmap(gw * (1 + 2 * n), gh, 2)
    for i, ch in enumerate(RAIN_GLYPHS):
        glyph = font.get_glyph(ord(ch))
        # glyph.bitmap is the font's shared sheet; locate this glyph's tile
        # from tile_index against the sheet's real geometry -- the layout
        # (strip vs grid) is an implementation detail we must not assume.
        src = glyph.bitmap
        per_row = src.width // gw
        sx = (glyph.tile_index % per_row) * gw
        sy = (glyph.tile_index // per_row) * gh
        for y in range(gh):
            for x in range(gw):
                v = src[sx + x, sy + y]
                if v:
                    sheet[(1 + i) * gw + x, y] = 1              # bright bank
                    if (x + y) % 2 == 0:
                        sheet[(1 + n + i) * gw + x, y] = 1      # dim bank
    return sheet, gw, gh, n


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        pal = _mono_palette()

        # digit bitmaps, built once: DIGITS index -> Bitmap
        self._small = [_digit_bitmap(rows, 1) for rows in DIGITS]
        self._big = [_digit_bitmap(rows, 2) for rows in DIGITS]
        self._pal = pal

        # --- rain layer (also the no-link base) ------------------------
        sheet, gw, gh, n = _rain_sheet(terminalio.FONT)
        self._rain_n = n
        cols, rows = km_weather.SCREEN_W // gw, km_weather.SCREEN_H // gh
        self._rain_grid = displayio.TileGrid(
            sheet, pixel_shader=pal, width=cols, height=rows,
            tile_width=gw, tile_height=gh, default_tile=0)
        self._rain_group = displayio.Group()
        self._rain_group.append(self._rain_grid)
        self._field = km_weather.RainField(random, cols, rows, glyphs=n)

        # --- bell wall layer -------------------------------------------
        self._wall_group = displayio.Group()
        self._wall_group.hidden = True
        self._wall_layout = None       # last-drawn layout list

        # --- marquee layer ---------------------------------------------
        self._marquee_group = displayio.Group()
        self._marquee_group.hidden = True
        self._marquee_epoch = None
        self._marquee_tile = None

        # --- badge layer (topmost) -------------------------------------
        self._badges = displayio.Group()
        self._rec = label.Label(terminalio.FONT, text=" REC ",
                                color=0x000000, background_color=0xFFFFFF)
        self._rec.anchor_point = (1.0, 0.0)
        self._rec.anchored_position = (km_weather.SCREEN_W, 0)
        self._rec.hidden = True
        self._submap = label.Label(terminalio.FONT, text="",
                                   color=0x000000, background_color=0xFFFFFF)
        self._submap.anchor_point = (1.0, 0.0)
        self._submap.anchored_position = (km_weather.SCREEN_W - 34, 0)
        self._submap.hidden = True
        self._nolink = label.Label(terminalio.FONT, text=" no link ",
                                   color=0x000000, background_color=0xFFFFFF)
        self._nolink.anchor_point = (1.0, 1.0)
        self._nolink.anchored_position = (km_weather.SCREEN_W,
                                          km_weather.SCREEN_H)
        self._nolink.hidden = True
        self._badges.append(self._rec)
        self._badges.append(self._submap)
        self._badges.append(self._nolink)

        for layer in (self._rain_group, self._wall_group,
                      self._marquee_group, self._badges):
            self.group.append(layer)
        display.root_group = self.group
        self._display = display

        # state caches -- write hardware only on change
        self._weather = None
        self._flags = (None, None)
        self._submap_text = ""
        self._clock = km_weather.FrameClock(FRAME_MS, ticks_ms(),
                                            ticks_add, ticks_diff)
        self._rain_acc = 0

    # ---- state ---------------------------------------------------------
    def set_weather(self, state):
        if state == self._weather:
            return
        self._weather = state
        self._rain_group.hidden = state == "ringing"
        self._wall_group.hidden = state != "ringing"
        self._nolink.hidden = state != "nolink"

    def set_bells(self, order):
        layout = km_weather.wall_layout(order)
        if layout == self._wall_layout:
            return
        self._wall_layout = layout
        # Rare change event: rebuild the layer's children with the panel's
        # auto-refresh suspended, so an async scan-out can never catch the
        # half-built (or empty) wall -- the 489c99a blank-frame bug class.
        self._display.auto_refresh = False
        try:
            while len(self._wall_group):
                self._wall_group.pop()
            for ws, size, x, y in layout:
                bank = self._big if size == "big" else self._small
                tg = displayio.TileGrid(bank[ws % 10],
                                        pixel_shader=self._pal, x=x, y=y)
                self._wall_group.append(tg)
        finally:
            self._display.auto_refresh = True

    def marquee(self, ws):
        while len(self._marquee_group):
            self._marquee_group.pop()
        tg = displayio.TileGrid(self._big[ws % 10], pixel_shader=self._pal,
                                x=km_weather.SCREEN_W,
                                y=(km_weather.SCREEN_H - km_weather.BIG_H) // 2)
        self._marquee_group.append(tg)
        self._marquee_tile = tg
        self._marquee_epoch = ticks_ms()
        self._marquee_group.hidden = False

    def set_flags(self, rec, submap):
        if (rec, submap) == self._flags:
            return
        self._flags = (rec, submap)
        if self._rec.hidden != (not rec):
            self._rec.hidden = not rec
        # Submap yields on collision (km_text's old priority rule), computed
        # in pixels: " [name] " at 6px/char, right-anchored at x=94, must
        # stay on screen left of REC's reserved corner -> len(name) <= 11.
        # Label.text has no equality short-circuit (docs/pad-timing.md), so
        # the text write goes through its own cache, not the flags tuple.
        text = " [" + submap + "] " if submap and len(submap) <= 11 else ""
        if text != self._submap_text:
            self._submap_text = text
            if text:
                self._submap.text = text
            self._submap.hidden = not text

    # ---- animation ------------------------------------------------------
    def tick(self, now):
        # ALL display mutation is gated behind the frame clock -- the main
        # loop is unthrottled (docs/pad-timing.md section 1), so anything
        # outside this gate would dirty the panel at loop frequency.
        frames = self._clock.advance(now)
        if not frames:
            return
        if self._marquee_epoch is not None:
            x = km_weather.marquee_x(ticks_diff(now, self._marquee_epoch))
            if x is None:
                self._marquee_group.hidden = True
                self._marquee_epoch = None
            elif self._marquee_tile.x != x:
                self._marquee_tile.x = x
        self._rain_acc += frames
        if self._rain_acc >= RAIN_DIV:
            self._rain_acc = 0
            if not self._rain_group.hidden:
                for col, row, kind, glyph_i in self._field.step():
                    if kind == "off":
                        self._rain_grid[col, row] = 0
                    else:
                        bank = _KIND_BANK[kind] * self._rain_n
                        self._rain_grid[col, row] = 1 + bank + glyph_i
```

- [ ] **Step 2: Syntax-check on the host**

Run: `cd /home/chris/src/keymaker && python -m py_compile firmware/pad/ui.py`
Expected: exit 0. (It cannot import on the host — displayio — but it must
parse. Full verification is the Task 8 bench pass.)

- [ ] **Step 3: Commit**

```bash
cd /home/chris/src/keymaker
git add firmware/pad/ui.py
git commit -m "feat: layered OLED Screen -- rain, bell wall, marquee, badges"
```

---

### Task 7: rewire Cockpit

**Files:**
- Modify: `firmware/apps/cockpit.py`

**Interfaces:**
- Consumes: `Screen.set_weather/set_bells/marquee/set_flags/tick` (Task 6);
  `km_weather.weather/update_stamps/bell_order` (Task 1).
- Produces: nothing new downstream. LED handling is untouched.

Changes, precisely:

1. Imports: drop `from km_text import header_line, marquee` and
   `from pad.ui import WIDTH_CHARS`; add `import km_weather` and keep
   `ticks_diff, ticks_ms`.
2. `__init__`: delete `self._header_text`, `self._focus_text`, `self._idle`;
   add `self._stamps = {}` and `self._last_active = None`.
3. Delete `_draw_text` entirely.
4. Add `_sync_screen` and call it from `on_msg`, `on_show`, and `tick`.

- [ ] **Step 1: Apply the edits**

```python
# firmware/apps/cockpit.py -- replace _draw_text and its wiring with:

    def _sync_screen(self, now):
        if not self.link.up:
            self._stamps.clear()
            self._last_active = None
            self.screen.set_weather("nolink")
            self.screen.set_flags(False, "")
            return
        urgent = self.ws.get("urgent") or []   # None-proof against bad msgs
        km_weather.update_stamps(self._stamps, urgent, now)
        # set_bells BEFORE set_weather: the wall must be populated before the
        # weather flip reveals it, or the panel can scan out an empty frame.
        self.screen.set_bells(km_weather.bell_order(self._stamps, ticks_diff))
        self.screen.set_weather(km_weather.weather(urgent, True))
        self.screen.set_flags(bool(self.flags.get("screencast")),
                              self.flags.get("submap") or "")
        active = self.ws.get("active")
        if (active is not None and self._last_active is not None
                and active != self._last_active):
            self.screen.marquee(active)
        if active is not None:
            self._last_active = active
```

`on_msg` ends with `self._draw_all(ticks_ms())` as today; `_draw_all`
becomes:

```python
    def _draw_all(self, now):
        self._draw_leds(now)
        self._sync_screen(now)
```

`tick` becomes:

```python
    def tick(self, now):
        for n in self.tracker.tick(now):
            self.link.send({"t": "key", "n": n, "act": "hold"})
        self._draw_leds(now)          # every pass: urgent pulse animation
        self._sync_screen(now)        # cheap: every setter diffs internally
        self.screen.tick(now)         # rain + marquee frame clock
```

`on_show` drops the two text-cache resets (the fields no longer exist) and
keeps the LED frame reset and `_draw_all`. The module docstring's OLED
sentence becomes: `OLED = weather display (rain / bell wall / marquee,
see docs/specs/2026-08-23-oled-weather-design.md).`

Note `_last_active = None` guards the boot case: the first `ws` snapshot
must not fire a marquee.

- [ ] **Step 2: Syntax-check and run the full suite**

```bash
cd /home/chris/src/keymaker
python -m py_compile firmware/apps/cockpit.py
python -m pytest -v
```
Expected: compile clean; all tests pass (nothing host-tested imports
cockpit, but the suite guards the shared modules it leans on).

- [ ] **Step 3: Commit**

```bash
cd /home/chris/src/keymaker
git add firmware/apps/cockpit.py
git commit -m "feat: Cockpit drives the weather screen; text OLED retired"
```

---

### Task 8: deploy, bench pass, docs

**Files:**
- Modify: `README.md` (the OLED bullet)

- [ ] **Step 1: Deploy to the pad**

Run: `cd /home/chris/src/keymaker && ./system/deploy-firmware.sh`
Expected: rsync completes, one clean reload fires. (The script owns the
autoreload pause — never rsync to CIRCUITPY by hand.)

- [ ] **Step 2: Hardware bench checklist**

Watch the pad through each of these; any flicker, strobe, or stuck frame is
a pad-timing §5 violation to fix before proceeding:

1. Daemon stopped (`systemctl --user stop keymaker`): rain falls, `no link`
   tag bottom-right, no flicker between frames.
2. Daemon started: tag clears, rain continues (calm).
3. `printf '\a'` in a terminal on another workspace: bell wall appears with
   that workspace's numeral big; rain gone.
4. Second bell on a different workspace: first numeral shrinks to the small
   row, newer one is big.
5. Visit the ringing windows: bells clear one by one; last clear returns
   rain.
6. Switch workspaces: marquee numeral wipes across, then the base state is
   intact beneath.
7. Start a screen share (or `wf-recorder`): `REC` badge top-right over rain
   AND over the bell wall; stop: badge clears.
8. Leave it in calm for five minutes: no drift, no creeping artifacts, rain
   keeps its rhythm.

- [ ] **Step 3: Update the README bullet**

Replace the OLED bullet under "What it does" with:

```markdown
- The OLED is a weather display: sparse digital rain while all is calm, and
  when terminal bells ring elsewhere, a wall of workspace numerals sized by
  recency — the newest bell largest. A workspace switch wipes a marquee
  numeral across the screen; a REC badge overlays everything while the
  screen is being captured. Design: docs/specs/2026-08-23-oled-weather-design.md.
```

- [ ] **Step 4: Commit and push**

```bash
cd /home/chris/src/keymaker
git add README.md
git commit -m "docs: README describes the OLED weather display"
git push
```

## Bench checklist (consolidated)

This is the residue of the work that no host test could settle — three code
reviews and the implementers surfaced questions only the physical pad can
answer. The branch is not finished until someone has walked this list at
the bench.

1. Daemon stopped (`systemctl --user stop keymaker`): rain falls, `no link`
   tag bottom-right, no flicker between frames.
2. Daemon started: tag clears, rain continues (calm).
3. `printf '\a'` in a terminal on another workspace: bell wall appears with
   that workspace's numeral big; rain gone.
4. Second bell on a different workspace: first numeral shrinks to the small
   row, newer one is big — confirm the ordering is newest-first, with the
   most recently belled workspace largest and leftmost.
5. Visit the ringing windows: bells clear one by one; last clear returns
   rain.
6. Switch workspaces repeatedly: the marquee numeral wipes across every
   time, not just most times, and the base state underneath is intact once
   it resolves.
7. Start a screen share (or `wf-recorder`): the `REC` badge appears
   top-right, legible over both rain and the bell wall; stop and confirm it
   clears. Separately, trigger a submap and confirm its badge is also
   legible over both rain and the bell wall, and that when both would
   apply at once the submap badge is the one that drops.
8. Leave it in calm for five minutes: no drift, no creeping artifacts, rain
   keeps its rhythm.
9. Look closely at individual rain glyphs: each should read as a whole,
   correctly-formed character, not a fragment of a neighboring one. Also
   check for a dead strip (roughly 4px) along the bottom of the panel,
   behind where the `no link` tag sits — its presence would mean
   `terminalio.FONT` is actually 6×12 on this CircuitPython build rather
   than the assumed 6×8, and the rain grid should be 21×5.
10. Judge whether the checkerboard dither behind each raindrop reads as
    "dim"/grey at arm's length, or just reads as noise. This is the 1-bit
    stand-in for a 50% grey trail — if it doesn't read, the rain has no
    depth.
11. Time the pad's boot. If startup is slow enough to notice, the cause is
    the roughly 8,500 interpreted `Bitmap` writes done at import to build
    the digit bitmaps, and the fix is pre-baking the upscaled bitmaps
    instead of computing them at boot.
12. While the bell wall is visible, force a change in bell order (e.g. bell
    a third workspace) and watch for a flicker or a blank frame during the
    rebuild — `auto_refresh = False` should suppress any visible scan-out
    mid-rebuild.
13. Watch the marquee's left edge as the numeral exits the screen: note
    whether a small sliver visibly pops off rather than sliding fully
    clear, and judge whether it's worth addressing.
14. Judge whether the display feels smooth or the loop feels strained at
    the current frame clock (`FRAME_MS = 50`, rain advancing every 4th
    frame). If it strains, reverting to `FRAME_MS = 100` / `RAIN_DIV = 4`
    halves the marquee's smoothness but is a two-constant change.
