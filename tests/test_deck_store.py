from keymakerd.deck_store import DeckStore


def test_round_trips_a_slot_map(tmp_path):
    s = DeckStore(tmp_path / "deck-slots.json")
    s.save({"tmux:@1": 0, "hypr:5f3a": 3})
    assert s.load() == {"tmux:@1": 0, "hypr:5f3a": 3}


def test_missing_file_loads_empty(tmp_path):
    assert DeckStore(tmp_path / "nope.json").load() == {}


def test_corrupt_file_loads_empty_rather_than_raising(tmp_path):
    p = tmp_path / "deck-slots.json"
    p.write_text("{not json")
    assert DeckStore(p).load() == {}


def test_rejects_entries_that_are_not_id_to_int_slot(tmp_path):
    p = tmp_path / "deck-slots.json"
    p.write_text('{"version":1,"slots":{"ok":2,"bad":"x","neg":-1,"huge":9999}}')
    assert DeckStore(p).load() == {"ok": 2}


def test_rejects_bool_slots_even_though_bool_is_an_int_subclass(tmp_path):
    p = tmp_path / "deck-slots.json"
    p.write_text('{"version":1,"slots":{"x":true,"ok":2}}')
    assert DeckStore(p).load() == {"ok": 2}


def test_duplicate_slot_values_keep_only_the_first(tmp_path):
    # Two ids mapping to the same slot survives naive validation, and
    # Deck.message then silently drops one -- a live window with no key for
    # its whole life. Keep the first (JSON key order), drop the rest.
    p = tmp_path / "deck-slots.json"
    p.write_text('{"version":1,"slots":{"first":0,"second":0,"third":1}}')
    assert DeckStore(p).load() == {"first": 0, "third": 1}


def test_save_is_atomic_leaving_no_tmp_behind(tmp_path):
    s = DeckStore(tmp_path / "deck-slots.json")
    s.save({"tmux:@1": 0})
    assert [f.name for f in tmp_path.iterdir()] == ["deck-slots.json"]
