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


def test_state_factor_orders_states_by_brightness():
    assert kp.state_factor("focused") == 1.0
    assert kp.state_factor("live") == kp.OCCUPIED_SCALE
    assert kp.state_factor("ghost") < kp.OCCUPIED_SCALE
    assert kp.state_factor("empty") == 0.0


def test_a_bell_pulses_between_live_and_focused_never_below_ambient():
    lo = kp.state_factor("bell", phase=0.0)
    hi = kp.state_factor("bell", phase=1.0)
    assert lo == kp.OCCUPIED_SCALE      # never dimmer than a live key
    assert hi == 1.0


def test_a_ghost_keeps_its_hue_on_the_darkest_cell_in_the_palette():
    # The one thing a ghost must still communicate is WHICH workspace it was.
    # #6d0a9e is Petroff 10's darkest led cell (channels 6d/0a/9e); its green
    # channel is the first thing to quantise away as the factor drops.
    rgb = kp.hex_to_int("6d0a9e")
    dim = kp.scale(rgb, kp.state_factor("ghost"))
    r, g, b = (dim >> 16) & 0xFF, (dim >> 8) & 0xFF, dim & 0xFF
    assert g > 0, "green quantised to zero: the hue is gone, not dimmed"
    assert b > r > g, "channel ordering must survive dimming"
    full_ratio = 0x6d / 0x9e
    assert abs(r / b - full_ratio) < 0.02, "hue drifted while dimming"


def test_white_neutral_survives_dimming_as_white():
    dim = kp.scale(kp.hex_to_int("ffffff"),
                   kp.state_factor("ghost"))
    r, g, b = (dim >> 16) & 0xFF, (dim >> 8) & 0xFF, dim & 0xFF
    assert r == g == b                          # still achromatic when dim


def test_deck_key_color_mirrors_the_existing_key_colour_helpers():
    # key_color and state_factor already own colour decisions so the
    # firmware only assigns. deck_key_color builds on that family; without
    # it the scale maths would sit in cockpit.py where nothing can test it.
    lit = kp.deck_key_color("focused", "e16000", 0.0)
    assert lit == kp.hex_to_int("e16000")
    assert kp.deck_key_color("empty", "e16000", 0.0) == 0x000000
    live = kp.deck_key_color("live", "e16000", 0.0)
    ghost = kp.deck_key_color("ghost", "e16000", 0.0)
    assert ((lit >> 16) & 0xFF) > ((live >> 16) & 0xFF) > ((ghost >> 16) & 0xFF)


def test_deck_key_color_pulses_a_bell_between_live_and_focused():
    lo = kp.deck_key_color("bell", "e16000", 0.0)
    hi = kp.deck_key_color("bell", "e16000", 1.0)
    assert lo == kp.deck_key_color("live", "e16000", 0.0)
    assert hi == kp.deck_key_color("focused", "e16000", 0.0)


# ---- split deck: top-half workspace keys, bottom-half window keys ----------

def test_ws_key_color_uses_the_workspace_hue_for_active_and_occupied():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("active", "e16000", pal) == kp.hex_to_int("e16000")
    assert kp.ws_key_color("occupied", "e16000", pal) == \
        kp.scale(kp.hex_to_int("e16000"), kp.OCCUPIED_SCALE)


def test_ws_key_color_urgent_and_empty_ignore_the_workspace_hue():
    # Urgency is an alert, not an identity: it must look the same on every key.
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("urgent", "e16000", pal, 1.0) == \
        kp.key_color("urgent", pal, 1.0)
    assert kp.ws_key_color("empty", "e16000", pal) == 0


def test_ws_key_color_without_a_hue_falls_back_to_theme_accent():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.ws_key_color("active", None, pal) == kp.key_color("active", pal)


def test_ctx_key_color_empty_slot_is_off():
    assert kp.ctx_key_color(None, {}) == 0


def test_ctx_key_color_active_full_others_dimmed():
    pal = {"red": "00FF00"}
    on = kp.ctx_key_color({"c": "e16000", "active": True, "bell": False}, pal)
    off = kp.ctx_key_color({"c": "e16000", "active": False, "bell": False}, pal)
    assert on == kp.hex_to_int("e16000")
    assert off == kp.scale(kp.hex_to_int("e16000"), kp.OCCUPIED_SCALE)


def test_ctx_key_color_bell_pulses_red_regardless_of_identity_hue():
    pal = {"red": "00FF00"}
    hi = kp.ctx_key_color({"c": "e16000", "active": False, "bell": True}, pal, 1.0)
    lo = kp.ctx_key_color({"c": "e16000", "active": False, "bell": True}, pal, 0.0)
    assert hi == 0x00FF00
    assert lo == kp.scale(0x00FF00, kp.urgent_factor(0.0))


def test_ctx_key_color_missing_hue_stays_dark_but_a_bell_still_alerts():
    # Fail closed on colour, never on the alert: an item that arrives without
    # an identity hue renders off, unless it is ringing.
    pal = {"red": "00FF00"}
    assert kp.ctx_key_color({"c": None, "active": True, "bell": False}, pal) == 0
    assert kp.ctx_key_color({"c": None, "active": False, "bell": True}, pal, 1.0) \
        == 0x00FF00
