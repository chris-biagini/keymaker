import pytest

from keymakerd.volume import parse_volume


def test_parse_plain():
    assert parse_volume("Volume: 0.35\n") == (0.35, False)


def test_parse_muted():
    assert parse_volume("Volume: 0.35 [MUTED]\n") == (0.35, True)


def test_parse_garbage_raises():
    with pytest.raises((ValueError, IndexError)):
        parse_volume("wpctl exploded")
