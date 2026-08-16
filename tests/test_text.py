from km_text import marquee


def test_short_text_unchanged():
    assert marquee("hi", 10, t_ms=999999) == "hi"


def test_starts_at_zero_during_lead_pause():
    assert marquee("abcdefghij", 5, t_ms=0) == "abcde"
    assert marquee("abcdefghij", 5, t_ms=1400) == "abcde"   # still in 1500ms pause


def test_scrolls_then_parks_at_end():
    s = "abcdefghij"                     # span = 5 positions
    mid = marquee(s, 5, t_ms=1500 + 2 * (1000 // 6))
    assert mid == s[2:7]
    end = marquee(s, 5, t_ms=1500 + 5 * (1000 // 6) + 100)
    assert end == "fghij"                # parked during tail pause
