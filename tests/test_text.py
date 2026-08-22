from km_text import header_line, marquee


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


def test_header_line_never_truncates_mode_even_under_badge_pressure():
    # The exact failing case from the round-3 report: badges + a wide page
    # number used to make set_header's blind slice eat "P10/12" down to "P1".
    line = header_line("nexus", ["REC", "MUTE", "[x]"], "P10/12", 21)
    assert "P10/12" in line
    assert len(line) == 21


def test_header_line_no_badges_is_unpadded_and_exact_width():
    line = header_line("nexus", [], "VOL", 21)
    assert line == "nexus" + " " * 13 + "VOL"
    assert len(line) == 21


def test_header_line_drops_badges_from_the_end_whole():
    # submap ("[a-very-long-submap-name]") drops before MUTE, and MUTE before
    # nothing survives past REC -- least important, dropped first.
    line = header_line("nexus", ["REC", "MUTE", "[a-very-long-submap-name]"], "P1/2", 21)
    assert "[a-very-long-submap-name]" not in line
    assert "MUTE" in line
    assert "REC" in line
    assert len(line) == 21

    tighter = header_line("nexus", ["REC", "MUTE", "[a-very-long-submap-name]"], "P1/2", 15)
    assert "MUTE" not in tighter
    assert "REC" in tighter
    assert "P1/2" in tighter
    assert len(tighter) == 15


def test_header_line_degenerate_host_plus_mode_too_wide_still_exact_width_no_raise():
    # host+mode+1 space (11) exceeds width (8); no badges to drop. host gets
    # truncated, mode stays intact, per the documented fallback.
    line = header_line("nexuslong", [], "P10/12", 8)
    assert len(line) == 8
    assert line.endswith("P10/12")


def test_header_line_degenerate_mode_alone_too_wide_returns_width_long_without_raising():
    line = header_line("nexus", ["REC"], "P10/12", 3)
    assert len(line) == 3


def test_header_line_always_exactly_width():
    cases = [
        ("nexus", [], "VOL", 21),
        ("nexus", ["REC"], "VOL", 21),
        ("nexus", ["REC", "MUTE"], "P1/2", 21),
        ("nexus", ["REC", "MUTE", "[x]"], "P10/12", 21),
        ("nexus", ["REC", "MUTE", "[x]"], "P99/99", 21),
        ("nexus", [], "P1/2", 5),
        ("", [], "", 0),
    ]
    for host, badges, mode, width in cases:
        assert len(header_line(host, badges, mode, width)) == width
