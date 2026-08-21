import km_palette as kp


def test_hex_to_int_with_and_without_hash():
    assert kp.hex_to_int("#faa968") == 0xFAA968
    assert kp.hex_to_int("FAA968") == 0xFAA968


def test_scale_halves_channels():
    assert kp.scale(0x804020, 0.5) == 0x402010
    assert kp.scale(0xFFFFFF, 0.0) == 0x000000


def test_key_color_states():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.key_color("active", pal) == 0xFF0000
    assert kp.key_color("occupied", pal) == kp.scale(0xFF0000, kp.OCCUPIED_SCALE)
    assert kp.key_color("empty", pal) == 0
    dim = kp.key_color("urgent", pal, phase=0.0)
    bright = kp.key_color("urgent", pal, phase=1.0)
    assert bright == 0x00FF00 and 0 < dim < bright


def test_key_color_missing_keys_fall_back_to_default():
    assert kp.key_color("active", {}) == kp.hex_to_int(kp.DEFAULT["accent"])


def test_ws_key_color_uses_workspace_color():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("active", "e14b00", pal) == 0xE14B00
    assert kp.ws_key_color("occupied", "e14b00", pal) == kp.scale(0xE14B00, kp.OCCUPIED_SCALE)


def test_ws_key_color_falls_back_without_color_or_for_urgent():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("active", None, pal) == kp.key_color("active", pal)
    assert kp.ws_key_color("urgent", "e14b00", pal, phase=1.0) == kp.key_color("urgent", pal, phase=1.0)
    assert kp.ws_key_color("empty", "e14b00", pal) == 0


def test_ctx_key_color_states():
    pal = {"accent": "FF0000", "red": "00FF00"}
    active = {"i": 1, "name": "a", "active": True, "bell": False}
    inactive = {"i": 2, "name": "b", "active": False, "bell": False}
    belled = {"i": 3, "name": "c", "active": False, "bell": True}
    assert kp.ctx_key_color(active, pal) == kp.hex_to_int(kp.INDEX_BINS[0])
    assert kp.ctx_key_color(inactive, pal) == kp.scale(
        kp.hex_to_int(kp.INDEX_BINS[1]), kp.OCCUPIED_SCALE)
    assert kp.ctx_key_color(belled, pal, phase=1.0) == 0x00FF00   # theme red, full
    assert kp.ctx_key_color(None, pal) == 0                       # empty slot: off


def test_ctx_key_color_index_wraps():
    pal = {}
    item = {"i": 7, "name": "x", "active": True, "bell": False}
    assert kp.ctx_key_color(item, pal) == kp.hex_to_int(kp.INDEX_BINS[0])


# --- the pad's usable light budget: measured on hardware, not derived ---
#
# BRIGHTNESS lives in firmware/pad/framework.py and is applied by neopixel to
# whatever byte we store, so the light a key emits is int(byte * OCCUPIED_SCALE)
# * BRIGHTNESS. These two tests pin the properties that were actually broken,
# rather than pinning OCCUPIED_SCALE itself (which the other tests reference
# symbolically and would therefore follow silently in any direction).
BRIGHTNESS = 0.3
FLOOR_PWM = 4          # Chris's comfort floor, measured 2026-08-21 via led-ramp
BYTE_AT_L_FLOOR = 56   # sRGB byte of OKLab L 0.341, the floor's lightness
BYTE_AT_L_TOP = 239    # sRGB byte of OKLab L 0.95, top of the LED band


def _emitted(byte):
    return int(int(byte * kp.OCCUPIED_SCALE) * BRIGHTNESS)


def test_occupied_keys_clear_the_measured_comfort_floor():
    assert _emitted(BYTE_AT_L_FLOOR) >= FLOOR_PWM


def test_occupied_keys_keep_enough_range_for_lightness_to_mean_anything():
    """At OCCUPIED_SCALE 0.25 the whole palette collapsed into PWM 4..17.

    Thirteen steps is not enough range for lightness to distinguish anything on
    the pad, which defeats the colorhash lightness floor on the one surface it
    was most wanted. Guard the span, not the constant.
    """
    span = _emitted(BYTE_AT_L_TOP) - _emitted(BYTE_AT_L_FLOOR)
    assert span >= 25, f"occupied span collapsed to {span} PWM steps"


def test_urgent_never_dimmer_than_an_occupied_key():
    """A bell that recedes below its neighbours mid-pulse is a broken alert."""
    pal = {"accent": "FF0000", "red": "00FF00"}
    dimmest_urgent = kp.key_color("urgent", pal, phase=0.0)
    occupied = kp.key_color("occupied", pal)
    assert (dimmest_urgent & 0xFF00) >> 8 >= (occupied & 0xFF0000) >> 16
    assert kp.urgent_factor(0.0) >= kp.OCCUPIED_SCALE
    assert kp.urgent_factor(1.0) == 1.0
