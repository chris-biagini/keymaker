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


def submap_badge(submap, max_len):
    """Compose the " [name] " submap badge, or "" when it must be dropped.

    Lives here rather than at the displayio edge because it is pure string
    logic and the drawing edge is not host-testable. The badge is
    right-anchored at a FIXED position that already reserves REC's corner,
    so the two can never contend: the only reason to drop it is a name too
    long to fit in the space left of that anchor. `max_len` is that budget
    in characters, computed from the panel geometry by the caller.
    """
    if not submap or len(submap) > max_len:
        return ""
    return " [" + submap + "] "


MARQUEE_MS = 600
MARQUEE_X = (SCREEN_W - BIG_W) // 2     # centred: reads as a notification,
MARQUEE_Y = (SCREEN_H - BIG_H) // 2     # not as the wall's left-field state


def marquee_visible(elapsed_ms):
    """True while the workspace numeral is held on screen, for MARQUEE_MS
    from the switch. Pure function of elapsed time, so a slow tick retires
    the numeral on schedule instead of lagging (docs/pad-timing.md
    section 3).

    This started life as a horizontal wipe (marquee_x, returning a moving
    left edge). Bench pass 2026-08-23 rejected it: sliding a 28x48 tile
    13px per frame dirties both the old and new rectangles while the SH1106
    scans out, which tore visibly. A held numeral is written once and then
    costs nothing, which is both what the panel wants and what section 5
    wants. The name "marquee" is kept because that is what the whole
    feature is called end to end.
    """
    return 0 <= elapsed_ms < MARQUEE_MS


_SPAWN_P = 0.35              # per-step chance of trying to start a new drop


class RainField:
    """Sparse matrix-rain over a cols x rows glyph grid, as pure data.

    step() returns only the cells that changed this frame -- the drawing
    edge applies them 1:1 as tile writes, so the delta list IS the
    hardware-touch budget (docs/pad-timing.md section 5). At most one drop
    per column; a drop is a head glyph descending with a trail of "dim"
    glyphs behind it, erased from the tail.
    """

    def __init__(self, rng, cols, rows, glyphs=16, max_drops=10,
                 trail_min=3, trail_span=3):
        # trail_min/trail_span set drop length: trail_min + randrange(span).
        # They are parameters rather than constants because the grid's height
        # depends on terminalio.FONT's real glyph box, which is 6x12 on
        # CircuitPython 10.x -- five rows, not the eight an assumed 6x8 would
        # give. At five rows the default 3-5 trail lights every drop's whole
        # column and the head/dim/off gradient stops reading as a trail at
        # all, so the drawing edge shortens it. Confirmed at the bench
        # 2026-08-23 by the ~4px dead strip below the grid.
        self.rng = rng
        self.cols = cols
        self.rows = rows
        self.glyphs = glyphs
        self.max_drops = max_drops
        self.trail_min = trail_min
        self.trail_span = trail_span
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
                                   "len": self.trail_min
                                          + self.rng.randrange(self.trail_span),
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
