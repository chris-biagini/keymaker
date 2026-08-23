from km_weather import weather, update_stamps, bell_order


def plain_diff(a, b):
    return a - b


def test_weather_states():
    assert weather([], link_up=True) == "calm"
    assert weather([3], link_up=True) == "ringing"
    assert weather([3, 5], link_up=True) == "ringing"
    assert weather([], link_up=False) == "nolink"
    # link state dominates: ringing info is stale without a link
    assert weather([3], link_up=False) == "nolink"


def test_update_stamps_stamps_new_bells_once():
    stamps = {}
    update_stamps(stamps, [3], now=100)
    assert stamps == {3: 100}
    update_stamps(stamps, [3], now=200)   # already ringing: stamp unchanged
    assert stamps == {3: 100}
    update_stamps(stamps, [3, 5], now=300)
    assert stamps == {3: 100, 5: 300}


def test_update_stamps_clears_silenced_bells():
    stamps = {3: 100, 5: 300}
    update_stamps(stamps, [5], now=400)
    assert stamps == {5: 300}
    update_stamps(stamps, [], now=500)
    assert stamps == {}


def test_bell_order_newest_first():
    assert bell_order({3: 100, 5: 300, 1: 200}, plain_diff) == [5, 1, 3]
    assert bell_order({}, plain_diff) == []
    assert bell_order({4: 700}, plain_diff) == [4]


def test_bell_order_tie_breaks_deterministically():
    # daemon snapshot after reconnect stamps several bells the same tick;
    # lower workspace first among equals so the wall is stable across calls
    assert bell_order({6: 100, 2: 100, 4: 100}, plain_diff) == [2, 4, 6]


def test_bell_order_survives_tick_wraparound():
    # adafruit_ticks wraps at 2**29; ticks_diff-style compare must still
    # order a stamp taken just before the wrap behind one taken just after
    period = 2 ** 29

    def wrap_diff(a, b):
        return ((a - b + period // 2) % period) - period // 2

    stamps = {1: period - 10, 2: 5}      # ws 2 stamped 15 ticks AFTER ws 1
    assert bell_order(stamps, wrap_diff) == [2, 1]


from km_weather import (SCREEN_W, SCREEN_H, BIG_W, BIG_H, SMALL_W, SMALL_H,
                        wall_layout)


def test_wall_layout_single_bell_is_one_big_numeral():
    assert wall_layout([3]) == [(3, "big", 4, 8)]


def test_wall_layout_two_bells():
    assert wall_layout([5, 2]) == [(5, "big", 4, 8), (2, "small", 40, 20)]


def test_wall_layout_six_bells_all_fit_on_screen():
    placed = wall_layout([6, 5, 4, 3, 2, 1])
    assert placed[0] == (6, "big", 4, 8)
    assert [p[0] for p in placed[1:]] == [5, 4, 3, 2, 1]
    assert [p[1] for p in placed[1:]] == ["small"] * 5
    for ws, size, x, y in placed:
        w = BIG_W if size == "big" else SMALL_W
        h = BIG_H if size == "big" else SMALL_H
        assert 0 <= x and x + w <= SCREEN_W
        assert 0 <= y and y + h <= SCREEN_H


def test_wall_layout_smalls_are_evenly_spaced():
    placed = wall_layout([1, 2, 3, 4])
    xs = [p[2] for p in placed[1:]]
    assert xs == [40, 58, 76]            # SMALL_W + 4 gap


def test_wall_layout_empty():
    assert wall_layout([]) == []


from km_weather import MARQUEE_MS, marquee_x


def test_marquee_enters_from_right_edge():
    assert marquee_x(0) == SCREEN_W


def test_marquee_moves_monotonically_left():
    xs = [marquee_x(t) for t in range(0, MARQUEE_MS, 50)]
    assert all(b <= a for a, b in zip(xs, xs[1:]))


def test_marquee_fully_exits_left_by_the_end():
    # last in-flight position: the numeral is at most partially on screen
    assert marquee_x(MARQUEE_MS - 1) <= 0


def test_marquee_over_and_not_started_return_none():
    assert marquee_x(MARQUEE_MS) is None
    assert marquee_x(MARQUEE_MS + 5000) is None
    assert marquee_x(-1) is None
