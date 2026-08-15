from pathlib import Path

from keymakerd.hyprland import HyprState, find_instance_dir, parse_event

WORKSPACES = [
    {"id": 1, "windows": 2}, {"id": 2, "windows": 0}, {"id": 5, "windows": 1},
]
ACTIVE_WS = {"id": 1}
WIN = {"class": "foot", "title": "vim ~/notes.md"}
CLIENTS = [
    {"address": "0x5f2280", "workspace": {"id": 5}},
    {"address": "0x5f9000", "workspace": {"id": 1}},
]


def test_parse_event():
    assert parse_event("workspace>>3") == ("workspace", "3")
    assert parse_event("activewindow>>foot,a,b") == ("activewindow", "foot,a,b")
    assert parse_event("garbage") is None


def test_find_instance_dir_picks_dir_with_socket(tmp_path):
    a = tmp_path / "hypr" / "aaa"; a.mkdir(parents=True)
    b = tmp_path / "hypr" / "bbb"; b.mkdir(parents=True)
    (b / ".socket.sock").touch()
    assert find_instance_dir(tmp_path) == b


def test_refresh_produces_ws_and_win_messages_once():
    s = HyprState()
    msgs = s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert {"t": "ws", "active": 1, "occupied": [1, 5], "urgent": []} in msgs
    assert {"t": "win", "cls": "foot", "title": "vim ~/notes.md"} in msgs
    assert s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS) == []   # no change, no msgs


def test_urgent_event_address_is_normalized_and_cleared_on_focus():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    needs_refresh, _ = s.handle_event("urgent", "5f2280")   # event has NO 0x prefix
    assert needs_refresh
    msgs = s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert msgs[0]["urgent"] == [5]
    msgs = s.refresh(WORKSPACES, {"id": 5}, WIN, CLIENTS)   # focusing ws 5 clears it
    assert msgs[0]["urgent"] == []


def test_submap_and_screencast_touch_flags_only():
    s = HyprState()
    assert s.handle_event("submap", "resize") == (False, True)
    assert s.submap == "resize"
    assert s.handle_event("submap", "resize") == (False, False)
    assert s.handle_event("screencast", "1,0") == (False, True)
    assert s.screencast is True


def test_snapshot_always_returns_both_messages():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    ts = sorted(m["t"] for m in s.snapshot())
    assert ts == ["win", "ws"]
