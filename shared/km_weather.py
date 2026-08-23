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
