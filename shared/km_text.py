"""Marquee windowing. Pure function of time; no state to corrupt."""


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
