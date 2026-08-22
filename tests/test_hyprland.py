from pathlib import Path

from keymakerd import hyprland
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


def test_refresh_produces_ws_message_once():
    s = HyprState()
    msgs = s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert {"t": "ws", "active": 1, "occupied": [1, 5], "urgent": [], "colors": {}, "names": {}} in msgs
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


def test_snapshot_always_returns_the_ws_message():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    ts = sorted(m["t"] for m in s.snapshot())
    assert ts == ["ws"]


def test_fnv1a32_matches_the_colorhash_contract():
    # The frozen vectors from ~/.local/share/chezmoi/colorhash/vectors.json, which
    # lab.html's runSelfTest and the bash wsid_hash check against too. If this drifts,
    # the pad and the status bar color the same session differently.
    from keymakerd.hyprland import fnv1a32
    assert fnv1a32("") == 0x811C9DC5
    assert fnv1a32("a") == 0xE40C292C
    assert fnv1a32("mirepoix") == 0x2F81FEBA
    assert fnv1a32("oracle") == 0xF4E20EF7
    assert fnv1a32("café") == 0xA82B5049      # NFC-normalized before encoding
    assert fnv1a32("recipe box") == 0x9A56501A


def test_fnv1a32_normalizes_to_nfc():
    # The same café spelled with a combining acute must hash identically. This is the
    # only part of the contract Python enforces that bash cannot.
    from keymakerd.hyprland import fnv1a32
    assert fnv1a32("café") == fnv1a32("café")


def test_ws_color_hashes_plain_name():
    from keymakerd.hyprland import ws_color
    assert ws_color("mirepoix") == "e16000"          # cell 6, led surface
    assert ws_color("oracle") == "ffbc63"            # cell 1, led surface
    # `light` is accepted and ignored: LEDs have no theme ground to contrast against.
    assert ws_color("mirepoix", light=True) == ws_color("mirepoix")
    assert ws_color("4") is None                     # unnamed: bare id, no color
    assert ws_color("") is None
    assert ws_color(None) is None


def test_ws_color_takes_the_led_surface_not_the_fill():
    # palette.json's five columns are not interchangeable. `bg` is Petroff's published
    # fill (#e76300 for cell 6); `led` is the WS2812 rendering (#e16000). Sending the
    # fill would push a color that was never checked against the pad's hardware.
    from keymakerd.hyprland import ws_color
    assert ws_color("mirepoix") != "e76300"
    assert ws_color("mirepoix") == "e16000"


def test_ws_color_is_none_without_a_palette(tmp_path, monkeypatch):
    # Fail closed: an unlit pad is obvious, a plausible-but-wrong palette is not.
    from keymakerd import hyprland
    monkeypatch.setattr(hyprland, "PALETTE_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(hyprland, "_LED_CACHE", None)
    assert hyprland.ws_color("mirepoix") is None


def test_session_name_sanitizes_like_ws_attach():
    # Twin of wsid_session_name in ~/oracle/scripts/workspace-identity-lib.
    from keymakerd.hyprland import session_name
    assert session_name("keymaker") == "keymaker"
    assert session_name("wacky sax") == "wacky-sax"
    assert session_name("a.b:c/d") == "a-b-c-d"
    assert session_name(" x ") == "x"
    assert session_name("note: draft") == "note-draft"
    assert session_name("ch. 2 notes") == "ch-2-notes"
    assert session_name("a..b") == "a-b"
    assert session_name("foo. bar") == "foo-bar"


def test_ws_color_hashes_sanitized_name():
    from keymakerd.hyprland import ws_color
    # "wacky sax" sanitizes to "wacky-sax"; fnv1a32("wacky-sax")=0xaecd57d7, mod 10 = 1.
    assert ws_color("wacky sax") == "ffbc63"
    assert ws_color("wacky sax") == ws_color("wacky-sax")


def test_refresh_includes_workspace_colors():
    s = HyprState()
    wss = [
        {"id": 2, "windows": 1, "name": "mirepoix"},
        {"id": 3, "windows": 1, "name": "oracle"},
        {"id": 4, "windows": 1, "name": "4"},
    ]
    msgs = s.refresh(wss, {"id": 2}, None, [])
    ws = next(m for m in msgs if m["t"] == "ws")
    assert ws["colors"] == {"2": "e16000", "3": "ffbc63"}


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


def test_renameworkspace_triggers_refresh():
    # Hyprland emits renameworkspace>>ID,NAME (verified live 2026-08-18).
    # Without it in REFRESH_EVENTS a rename only reaches the pad by luck,
    # riding whatever unrelated event fires next.
    from keymakerd.hyprland import HyprState
    s = HyprState()
    assert s.handle_event("renameworkspace", "1,wacky-sax") == (True, False)


DECK_CLIENTS = [
    {"address": "0xaa", "class": "ws-mirepoix", "workspace": {"id": 2, "name": "mirepoix"}},
    {"address": "0xbb", "class": "firefox",     "workspace": {"id": 2, "name": "mirepoix"}},
    {"address": "0xcc", "class": "foot",        "workspace": {"id": 1, "name": "bonsai"}},
    {"address": "0xdd", "class": "foot",        "workspace": {"id": 4, "name": "4"}},
]
DECK_TWINS = [
    {"id": "tmux:@2", "s": "mirepoix", "i": 1, "n": "rails", "active": True, "bell": False},
    {"id": "tmux:@5", "s": "phoneonly", "i": 1, "n": "away", "active": False, "bell": False},
]


def test_only_signalling_locally_reachable_windows_earn_a_key():
    got = hyprland.deck_windows(DECK_TWINS, DECK_CLIENTS)
    ids = [g["id"] for g in got]
    assert "tmux:@2" in ids            # tmux window in a ws-* session
    assert "hypr:0xcc" in ids          # bare foot, signals via Hyprland bell
    assert "hypr:0xdd" in ids          # bare foot on an unnamed workspace
    assert "hypr:0xbb" not in ids      # firefox cannot signal
    assert "tmux:@5" not in ids        # no local client to jump to
    assert "hypr:0xaa" not in ids      # the ws-* client itself is not a key


def test_tmux_windows_take_the_workspace_of_their_ws_client():
    got = {g["id"]: g for g in hyprland.deck_windows(DECK_TWINS, DECK_CLIENTS)}
    assert got["tmux:@2"]["ws"] == "mirepoix"
    assert got["tmux:@2"]["n"] == "1 rails"       # index carried into the label


def test_deck_windows_orders_by_workspace_id_not_name():
    # Chris's actual layout (2026-08-20): id 1 bonsai, id 2 mirepoix, id 3
    # colorhash. Alphabetically that's bonsai, colorhash, mirepoix -- which
    # would put colorhash on key 2 instead of key 3, contradicting Hyprland's
    # on-screen left-to-right workspace order. deck_windows must sort by
    # workspace id, not name.
    clients = [
        {"address": "0x1", "class": "ws-bonsai", "workspace": {"id": 1, "name": "bonsai"}},
        {"address": "0x2", "class": "ws-mirepoix", "workspace": {"id": 2, "name": "mirepoix"}},
        {"address": "0x3", "class": "ws-colorhash", "workspace": {"id": 3, "name": "colorhash"}},
    ]
    twins = [
        {"id": "tmux:@c", "s": "colorhash", "i": 1, "n": "app", "active": False, "bell": False},
        {"id": "tmux:@m", "s": "mirepoix", "i": 1, "n": "rails", "active": False, "bell": False},
        {"id": "tmux:@b", "s": "bonsai", "i": 1, "n": "notes", "active": False, "bell": False},
    ]
    got = hyprland.deck_windows(twins, clients)
    assert [g["id"] for g in got] == ["tmux:@b", "tmux:@m", "tmux:@c"]


def test_deck_windows_sessionless_terminal_follows_its_workspaces_tmux_windows():
    clients = [
        {"address": "0x1", "class": "ws-mirepoix", "workspace": {"id": 1, "name": "mirepoix"}},
        {"address": "0x2", "class": "foot", "workspace": {"id": 1, "name": "mirepoix"}},
        {"address": "0x3", "class": "foot", "workspace": {"id": 2, "name": "bonsai"}},
    ]
    twins = [{"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "rails",
              "active": False, "bell": False}]
    got = hyprland.deck_windows(twins, clients)
    assert [g["id"] for g in got] == ["tmux:@1", "hypr:0x2", "hypr:0x3"]


def test_unnamed_workspaces_render_white_not_nothing():
    got = hyprland.deck_windows(DECK_TWINS, DECK_CLIENTS)
    colors = hyprland.deck_colors(got)
    assert colors["mirepoix"] == "e16000"
    assert colors["4"] == "ffffff"                # bare-digit name: no identity


def test_ws_color_itself_is_unchanged_and_still_returns_none():
    # The neutral belongs to the deck path only; the workspace pill still wants
    # None for an unnamed workspace so it can render nothing at all.
    assert hyprland.ws_color("4") is None


def test_tmux_flag_wins_where_a_session_exists():
    twins = [{"id": "tmux:@2", "s": "mirepoix", "i": 1, "n": "rails",
              "active": False, "bell": True}]
    # One BEL fires BOTH channels: tmux flags the window and foot rings the
    # system bell for the whole ws-* client. Counting both would smear the alert
    # across every key of that session.
    got = hyprland.deck_bells(twins, {"aa"}, DECK_CLIENTS)
    assert got == {"tmux:@2"}
    assert "hypr:0xaa" not in got


def test_hyprland_bell_is_authoritative_only_for_sessionless_clients():
    assert hyprland.deck_bells([], {"cc"}, DECK_CLIENTS) == {"hypr:0xcc"}


def test_no_bells_is_an_empty_set_not_none():
    assert hyprland.deck_bells([], set(), DECK_CLIENTS) == set()


def test_deck_windows_survives_a_client_with_no_workspace_id():
    # Hyprland always supplies an integer id for a normal client, but deck_windows
    # SORTS on it, so a missing one would raise TypeError against real ints.
    # _poll_deck catches Exception, so the symptom would be a deck that silently
    # stops updating -- degrade to "sorts last" instead of crashing.
    clients = [
        {"address": "0xc", "class": "foot", "workspace": {"name": "x"}, "title": "no-id"},
        {"address": "0xd", "class": "foot", "workspace": {"id": 1, "name": "y"},
         "title": "has-id"},
    ]
    got = hyprland.deck_windows([], clients)
    assert [w["n"] for w in got] == ["has-id", "no-id"]


def test_deck_windows_rejects_a_bool_workspace_id():
    # bool is an int subclass, so isinstance(True, int) passes a naive guard and
    # True would sort as workspace 1 rather than last.
    clients = [
        {"address": "0xc", "class": "foot", "workspace": {"id": True, "name": "b"},
         "title": "boolish"},
        {"address": "0xd", "class": "foot", "workspace": {"id": 2, "name": "y"},
         "title": "real"},
    ]
    got = hyprland.deck_windows([], clients)
    assert [w["n"] for w in got] == ["real", "boolish"]


def test_deck_windows_survives_a_null_workspace():
    # Hyprland can send "workspace": null. _ws_sort_id guards for exactly this,
    # but the sibling `.get("name", "")` call used to run on the unguarded
    # object -- an AttributeError there freezes the deck via _poll_deck's broad
    # except. It should still earn a slot, sorted last.
    clients = [
        {"address": "0xc", "class": "foot", "workspace": None, "title": "null-ws"},
        {"address": "0xd", "class": "foot", "workspace": {"id": 2, "name": "y"},
         "title": "real"},
    ]
    got = hyprland.deck_windows([], clients)
    assert [w["n"] for w in got] == ["real", "null-ws"]
