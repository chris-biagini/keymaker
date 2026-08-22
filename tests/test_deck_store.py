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


def test_save_is_atomic_leaving_no_tmp_behind(tmp_path):
    s = DeckStore(tmp_path / "deck-slots.json")
    s.save({"tmux:@1": 0})
    assert [f.name for f in tmp_path.iterdir()] == ["deck-slots.json"]
