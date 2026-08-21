"""Color math for the pad. Pure: no CircuitPython imports."""

DEFAULT = {
    "name": "default",
    "accent": "88CCFF", "bg": "111111", "fg": "DDDDDD",
    "red": "FF5555", "muted": "446677",
}

# Occupied keys are the DISPLAY, not an indicator. Under the colorhash blocks
# design it is colour that tells sessions apart, so the non-active keys carry
# the primary information and need enough light to render it.
#
# Two facts set this number, both measured on the pad 2026-08-21 (led-ramp):
#
#  1. scale() multiplies a GAMMA-ENCODED byte, so a factor f changes emitted
#     light by roughly f**2.2 -- not f. At 0.25 an occupied key emitted ~4.7%
#     of the active key's light, and after BRIGHTNESS the whole palette
#     collapsed into PWM 4..17: a 13-step window in which lightness could not
#     function as a distinguishing channel at all.
#  2. Chris's comfort floor -- the dimmest a lighted block should be, judged
#     at a glance from his seat in his room, NOT a detection threshold -- is
#     PWM 4 of 255. Detection runs at or below PWM 1; 4 is the design number.
#
# 0.55 widens the window to PWM 9..39 and drops the usable lightness floor from
# OKLab L 0.341 to 0.218. Raising it further keeps helping (0.70 -> 11..49) at
# the cost of contrast against the active key.
OCCUPIED_SCALE = 0.55

# An alert must never sit DIMMER than ambient. The urgent pulse used to run
# 0.25 -> 1.0, which was above the old occupied level for its whole cycle; with
# occupied at 0.55 a fixed 0.25 floor would make the bell key recede below its
# neighbours for part of every pulse. Tie the floor to OCCUPIED_SCALE so the
# two cannot drift apart again.
URGENT_FLOOR = OCCUPIED_SCALE


def urgent_factor(phase):
    """Pulse factor for a bell/urgent key: never below an occupied key."""
    return URGENT_FLOOR + (1.0 - URGENT_FLOOR) * phase

# Canonical Okabe-Ito hues, canonical order — window index N always gets bin N,
# on the pad AND in the tmux status bar (which uses the hue-locked WSID
# variants of these same bins). LEDs are emissive, so the saturated canonical
# values render truest; terminal text needs the contrast-tuned variants.
INDEX_BINS = ("E69F00", "56B4E9", "009E73", "F0E442", "0072B2", "D55E00")


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
        return scale(_c(pal, "red"), urgent_factor(phase))
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


def ctx_key_color(item, pal, phase=0.0):
    """Bottom-half key color for one ctx slot item; None (empty slot) -> off."""
    if item is None:
        return 0
    if item.get("bell"):
        return scale(_c(pal, "red"), urgent_factor(phase))
    c = hex_to_int(INDEX_BINS[(item["i"] - 1) % len(INDEX_BINS)])
    if item.get("active"):
        return c
    return scale(c, OCCUPIED_SCALE)
