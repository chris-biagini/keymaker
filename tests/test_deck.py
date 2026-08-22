import km_deck


def w(wid, ws="mirepoix", n="1 rails"):
    return {"id": wid, "ws": ws, "n": n}


def test_windows_take_lowest_free_slot_in_deterministic_order():
    d = km_deck.Deck()
    d.update([w("tmux:@3", "mirepoix", "2 specs"), w("tmux:@1", "colorhash", "1 lab")])
    # Cold start sorts by (ws, n) so a restart is reproducible, not arbitrary.
    assert d.slots == {"tmux:@1": 0, "tmux:@3": 1}


def test_a_window_holds_its_slot_when_others_come_and_go():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "1")])
    assert d.slots["tmux:@2"] == 1
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "1"), w("tmux:@9", "a", "2")])
    assert d.slots["tmux:@2"] == 1          # untouched by an unrelated open
    assert d.slots["tmux:@9"] == 2


def test_closing_a_window_ghosts_its_slot():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "2 specs")])
    d.update([w("tmux:@1", "a", "1")])
    assert "tmux:@2" not in d.slots
    assert d.ghosts[1] == {"ws": "b", "n": "2 specs"}


def test_a_new_window_reclaims_the_lowest_free_slot_ghost_or_not():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "2"), w("tmux:@3", "c", "3")])
    d.update([w("tmux:@1", "a", "1"), w("tmux:@3", "c", "3")])   # slot 1 ghosts
    d.update([w("tmux:@1", "a", "1"), w("tmux:@3", "c", "3"), w("tmux:@4", "d", "4")])
    assert d.slots["tmux:@4"] == 1          # ghost slot beats the never-used slot 3
    assert 1 not in d.ghosts                # claiming clears the ghost


def test_dismiss_clears_a_ghost_and_reports_whether_it_did():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1")])
    d.update([])
    assert d.dismiss(0) is True
    assert d.ghosts == {}
    assert d.dismiss(0) is False            # nothing there now
    assert d.dismiss(11) is False


def test_restored_slots_are_honoured_and_dead_ids_dropped():
    d = km_deck.Deck({"tmux:@1": 5, "tmux:@99": 2})
    d.update([w("tmux:@1", "a", "1"), w("tmux:@7", "b", "1")])
    assert d.slots["tmux:@1"] == 5          # restored position survives
    assert d.slots["tmux:@7"] == 0          # lowest free
    assert "tmux:@99" not in d.slots        # gone at first update
    assert d.ghosts == {}                   # a restart never fabricates ghosts


def test_beyond_twelve_windows_keep_allocating_into_later_pages():
    d = km_deck.Deck()
    d.update([w("tmux:@%d" % i, "a", str(i)) for i in range(20)])
    assert sorted(d.slots.values()) == list(range(20))
