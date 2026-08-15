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


_EMPTY_CELL = ".      "


def ctx_legend(items, rows=((1, 2, 3), (4, 5, 6))):
    """Two 21-char OLED lines labelling the bottom key rows.

    Cell = index digit, marker (! bell > * active > space), name cut to 4,
    trailing space — the marker hugs its index so cells never collide.
    """
    by_i = {}
    for it in items:
        by_i[it["i"]] = it
    lines = []
    for row in rows:
        cells = []
        for i in row:
            it = by_i.get(i)
            if it is None:
                cells.append(_EMPTY_CELL)
            else:
                m = "!" if it.get("bell") else ("*" if it.get("active") else " ")
                name = it.get("name", "")[:4]
                cells.append(str(i) + m + name + " " * (5 - len(name)))
        lines.append("".join(cells))
    return lines
