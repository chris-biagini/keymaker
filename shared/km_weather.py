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
