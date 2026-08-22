"""Marquee windowing and OLED header composition. Pure; no state to corrupt."""


def header_line(host, badges, mode, width):
    """Compose the OLED header, reserving the mode and truncating badges.

    The mode is positional information; badges are ambient state. So badges get
    dropped WHOLE, from the least important end, until the line fits -- never
    truncated mid-word, and never at the mode's expense. Truncating the mode
    turns "P10/12" into "P1": a page number that reads correct and is wrong,
    which is worse than showing no badges at all.

    `badges` is a list in most-to-least-important order (e.g. ["REC", "MUTE",
    "[submap]"]); they are dropped from the END of the list first, so the
    transient submap indicator goes before REC -- an unnoticed screencast is
    the costliest badge to miss.

    Degenerate case: if `host` and `mode` alone (plus the one separating
    space) already exceed `width`, there is no badge left to drop and no
    correct way to keep both whole. `host` is truncated from the right in
    that case -- host is a fixed, memorized label ("nexus"); mode is a live
    number nobody has memorized, so it is the one thing that must never lie.
    If even `mode` alone doesn't fit, this function still never raises: it
    returns some width-`width` string rather than a correct one, because at
    that point no composition strategy can convey the header regardless.

    Always returns a string of exactly `width` characters -- callers such as
    `Screen.set_header`'s slice-then-pad become a no-op rather than a silent
    corrupter of a well-formed line.
    """
    bl = list(badges)

    def compose(candidate_badges):
        prefix = host + "".join(" " + b for b in candidate_badges)
        return prefix, width - len(prefix) - len(mode)

    prefix, pad = compose(bl)
    while pad < 1 and bl:
        bl.pop()
        prefix, pad = compose(bl)
    if pad < 1:
        # No badges left and host+mode+1space still doesn't fit. Truncate
        # host, never mode.
        host_trunc = host[:max(0, width - 1 - len(mode))]
        prefix = host_trunc
        pad = width - len(prefix) - len(mode)
        if pad < 1:
            # mode alone is wider than the screen -- nothing left to
            # sacrifice but mode itself. Guarantee a width-long string
            # rather than raise.
            return (host_trunc + " " + mode)[:width].ljust(width)
    return prefix + " " * pad + mode


def marquee(text, width, t_ms, cps=6, pause_ms=1500):
    if len(text) <= width:
        return text
    span = len(text) - width
    step_ms = 1000 // cps
    cycle = pause_ms + span * step_ms + pause_ms
    t = t_ms % cycle
    if t < pause_ms:
        off = 0
    elif t < pause_ms + span * step_ms:
        off = (t - pause_ms) // step_ms
    else:
        off = span
    return text[off:off + width]
