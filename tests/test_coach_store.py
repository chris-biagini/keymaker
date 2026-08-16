import json

from keymakerd.coach_store import CoachStore


def _sess(stage=1, score=0.9):
    return {"stage": stage, "bpm": 95, "swing": None, "greens": 14,
            "ambers": 1, "reds": 0, "misses": 1, "strays": 0,
            "score": score, "duration_ms": 40000}


def test_missing_file_is_empty(tmp_path):
    assert CoachStore(tmp_path / "coach.json").load() == []


def test_append_stamps_ts_and_roundtrips(tmp_path):
    store = CoachStore(tmp_path / "coach.json")
    store.append(_sess())
    hist = store.load()
    assert len(hist) == 1
    assert hist[0]["score"] == 0.9
    assert "ts" in hist[0]
    raw = json.loads((tmp_path / "coach.json").read_text())
    assert raw["version"] == 1


def test_malformed_file_recovers(tmp_path):
    p = tmp_path / "coach.json"
    p.write_text("{nope")
    store = CoachStore(p)
    assert store.load() == []
    store.append(_sess())                      # and it can still write
    assert len(store.load()) == 1


def test_state_msg_shape(tmp_path):
    store = CoachStore(tmp_path / "coach.json")
    for _ in range(3):
        store.append(_sess())
    msg = store.state_msg()
    assert msg["t"] == "coach"
    assert msg["unlocked"] == 2
    assert msg["graduated"] is False
    assert msg["stages"]["1"]["best"] == 0.9


def test_creates_parent_dirs(tmp_path):
    store = CoachStore(tmp_path / "deep" / "coach.json")
    store.append(_sess())
    assert len(store.load()) == 1
