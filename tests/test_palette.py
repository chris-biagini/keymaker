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
    assert kp.key_color("occupied", pal) == kp.scale(0xFF0000, 0.12)
    assert kp.key_color("empty", pal) == 0
    dim = kp.key_color("urgent", pal, phase=0.0)
    bright = kp.key_color("urgent", pal, phase=1.0)
    assert bright == 0x00FF00 and 0 < dim < bright


def test_key_color_missing_keys_fall_back_to_default():
    assert kp.key_color("active", {}) == kp.hex_to_int(kp.DEFAULT["accent"])
