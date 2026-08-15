"""Color math for the pad. Pure: no CircuitPython imports."""

DEFAULT = {
    "name": "default",
    "accent": "88CCFF", "bg": "111111", "fg": "DDDDDD",
    "red": "FF5555", "muted": "446677",
}

OCCUPIED_SCALE = 0.25


def hex_to_int(s):
    s = s.lstrip("#")
    return int(s[:6], 16)


def scale(rgb, f):
    r = int(((rgb >> 16) & 0xFF) * f)
    g = int(((rgb >> 8) & 0xFF) * f)
    b = int((rgb & 0xFF) * f)
    return (r << 16) | (g << 8) | b


def _c(pal, key):
    return hex_to_int(pal.get(key) or DEFAULT[key])


def key_color(state, pal, phase=0.0):
    if state == "active":
        return _c(pal, "accent")
    if state == "occupied":
        return scale(_c(pal, "accent"), OCCUPIED_SCALE)
    if state == "urgent":
        return scale(_c(pal, "red"), 0.25 + 0.75 * phase)
    return 0


def ws_key_color(state, ws_hex, pal, phase=0.0):
    """Per-workspace color for active/occupied; theme fallback otherwise."""
    if ws_hex is None or state in ("urgent", "empty"):
        return key_color(state, pal, phase)
    c = hex_to_int(ws_hex)
    if state == "active":
        return c
    if state == "occupied":
        return scale(c, OCCUPIED_SCALE)
    return 0
