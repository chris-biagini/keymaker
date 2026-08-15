from km_keys import KeyTracker


def test_short_press_is_tap():
    t = KeyTracker(hold_ms=400)
    t.press(3, 1000)
    assert t.tick(1100) == []
    assert t.release(3, 1200) == "tap"


def test_long_press_fires_hold_once_and_release_is_not_tap():
    t = KeyTracker(hold_ms=400)
    t.press(3, 1000)
    assert t.tick(1500) == [3]
    assert t.tick(1600) == []          # fires only once
    assert t.release(3, 1700) is None  # hold already consumed it


def test_release_without_press_is_none():
    assert KeyTracker().release(9, 50) is None


def test_custom_diff_supports_tick_wrap():
    # device passes adafruit_ticks.ticks_diff; emulate a wrapping counter
    t = KeyTracker(hold_ms=400, diff=lambda a, b: (a - b) % 2**16)
    t.press(1, 2**16 - 100)
    assert t.tick(350) == [1]          # wrapped: elapsed 450ms
