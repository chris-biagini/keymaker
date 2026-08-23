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
