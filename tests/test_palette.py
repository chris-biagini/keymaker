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
