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


def test_ctx_legend_cells():
    from km_text import ctx_legend
    items = [
        {"i": 1, "name": "vim", "active": False, "bell": False},
        {"i": 2, "name": "server", "active": True, "bell": False},
        {"i": 4, "name": "rails", "active": False, "bell": True},
    ]
    l1, l2 = ctx_legend(items)
    assert l1 == "1 vim  2*serv .      "
    assert l2 == "4!rail .      .      "
    assert len(l1) == len(l2) == 21


def test_ctx_legend_empty_and_full():
    from km_text import ctx_legend
    assert ctx_legend([]) == [".      " * 3, ".      " * 3]
    items = [{"i": i, "name": "abcdefg", "active": False, "bell": False}
             for i in range(1, 7)]
    l1, l2 = ctx_legend(items)
    assert l1 == "1 abcd 2 abcd 3 abcd "
    assert l2 == "4 abcd 5 abcd 6 abcd "
