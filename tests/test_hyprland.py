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
    assert {"t": "ws", "active": 1, "occupied": [1, 5], "urgent": [], "colors": {}, "names": {}} in msgs
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


def test_bell_event_is_treated_as_urgent():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    needs_refresh, _ = s.handle_event("bell", "5f2280")
    assert needs_refresh
    msgs = s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert msgs[0]["urgent"] == [5]
    msgs = s.refresh(WORKSPACES, {"id": 5}, WIN, CLIENTS)
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


def test_posix_cksum_matches_coreutils():
    # Vectors generated with: printf '%s' NAME | cksum
    from keymakerd.hyprland import posix_cksum
    assert posix_cksum(b"mirepoix") == 1440240219
    assert posix_cksum(b"oracle") == 3207680428
    assert posix_cksum(b"1") == 433426081
    assert posix_cksum(b"wacky-sax") == 840069188


def test_ws_color_hashes_plain_name():
    from keymakerd.hyprland import ws_color
    assert ws_color("mirepoix") == "e14b00"          # bin 5 dark
    assert ws_color("oracle") == "5ab4ff"            # bin 1 dark
    assert ws_color("mirepoix", light=True) == "78694b"
    assert ws_color("4") is None                     # unnamed: bare id, no color
    assert ws_color("") is None
    assert ws_color(None) is None


def test_refresh_includes_workspace_colors():
    s = HyprState()
    wss = [
        {"id": 2, "windows": 1, "name": "mirepoix"},
        {"id": 3, "windows": 1, "name": "oracle"},
        {"id": 4, "windows": 1, "name": "4"},
    ]
    msgs = s.refresh(wss, {"id": 2}, None, [])
    ws = next(m for m in msgs if m["t"] == "ws")
    assert ws["colors"] == {"2": "e14b00", "3": "5ab4ff"}


def test_ws_label_plain_names():
    from keymakerd.hyprland import ws_label
    assert ws_label("mirepoix") == "mirepoix"
    assert ws_label("4") is None            # unnamed: bare id
    assert ws_label("") is None
    assert ws_label(None) is None


def test_refresh_includes_workspace_names():
    s = HyprState()
    wss = [
        {"id": 2, "windows": 1, "name": "mirepoix"},
        {"id": 3, "windows": 1, "name": "oracle"},
        {"id": 4, "windows": 1, "name": "4"},
    ]
    msgs = s.refresh(wss, {"id": 2}, None, [])
    ws = next(m for m in msgs if m["t"] == "ws")
    assert ws["names"] == {"2": "mirepoix", "3": "oracle"}


FG_CLIENTS = [
    {"address": "0xaaa", "class": "firefox", "workspace": {"id": 2}, "focusHistoryID": 0},
    {"address": "0xbbb", "class": "ws-mirepoix", "workspace": {"id": 2}, "focusHistoryID": 3},
    {"address": "0xccc", "class": "ws-scratch", "workspace": {"id": 2}, "focusHistoryID": 1},
    {"address": "0xddd", "class": "ws-oracle", "workspace": {"id": 3}, "focusHistoryID": 5},
    {"address": "0xeee", "class": "foot", "workspace": {"id": 1}, "focusHistoryID": 4},
]


def test_refresh_builds_per_workspace_ws_map():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, FG_CLIENTS)
    # ws 2 has two ws- windows: lowest focusHistoryID (most recent) wins
    assert s.fg == {
        2: {"addr": "0xccc", "cls": "ws-scratch"},
        3: {"addr": "0xddd", "cls": "ws-oracle"},
    }                                       # plain foot and firefox excluded


def test_refresh_fg_map_tolerates_missing_fields():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, [
        {"class": "ws-x"},                                  # no workspace
        {"class": "ws-y", "workspace": {"id": 4}},          # no focusHistoryID, no address
    ])
    assert s.fg == {4: {"addr": "", "cls": "ws-y"}}


def test_refresh_tracks_active_window_address():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, {"class": "foot", "title": "t", "address": "0xf00"}, [])
    assert s.addr == "0xf00"
    s.refresh(WORKSPACES, ACTIVE_WS, None, [])    # no active window
    assert s.addr == ""
