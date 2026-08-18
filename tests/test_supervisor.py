import asyncio
import json
import os
from pathlib import Path

import pytest

import km_proto
from keymakerd.__main__ import Config, Supervisor

WORKSPACES = [{"id": 1, "windows": 1}, {"id": 3, "windows": 2}]


class FakeHypr:
    """Serves .socket.sock (requests) and .socket2.sock (events)."""

    def __init__(self):
        self.dispatched = []
        self.event_writer = None

    async def start(self, instance_dir):
        instance_dir.mkdir(parents=True)
        await asyncio.start_unix_server(self._req, path=str(instance_dir / ".socket.sock"))
        await asyncio.start_unix_server(self._ev, path=str(instance_dir / ".socket2.sock"))

    async def _req(self, reader, writer):
        cmd = (await reader.read(1024)).decode()
        if cmd == "j/workspaces":
            writer.write(json.dumps(WORKSPACES).encode())
        elif cmd == "j/activeworkspace":
            writer.write(b'{"id": 3}')
        elif cmd == "j/activewindow":
            writer.write(b'{"class": "foot", "title": "hello"}')
        elif cmd == "j/clients":
            writer.write(b"[]")
        else:
            self.dispatched.append(cmd)
            writer.write(b"ok")
        await writer.drain()
        writer.close()

    async def _ev(self, reader, writer):
        self.event_writer = writer


@pytest.fixture
def pad():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def _read_msgs(master):
    codec = km_proto.LineCodec()
    try:
        return codec.feed(os.read(master, 65536))
    except BlockingIOError:
        return []


def test_snapshot_on_connect_and_key_dispatch(pad, tmp_path):
    master, slave_path = pad

    async def scenario():
        hypr = FakeHypr()
        await hypr.start(tmp_path / "hypr" / "fake0")
        cfg = Config(device=slave_path, runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        task = asyncio.create_task(sup.run())
        await asyncio.sleep(0.4)
        msgs = _read_msgs(master)
        # pad presses key 2 (0-based) → workspace 3, then holds key 0 → move to ws 1
        os.write(master, km_proto.encode({"t": "key", "n": 2, "act": "tap"}))
        os.write(master, km_proto.encode({"t": "key", "n": 0, "act": "hold"}))
        await asyncio.sleep(0.3)
        task.cancel()
        return msgs, hypr.dispatched

    msgs, dispatched = asyncio.run(scenario())
    types = [m["t"] for m in msgs]
    for expected in ("hello", "ws", "win", "flags"):
        assert expected in types
    # The link-up snapshot goes out before the first Hyprland refresh, so the
    # FIRST ws msg is the default state; the refreshed one arrives last.
    ws = [m for m in msgs if m["t"] == "ws"][-1]
    assert ws == {"t": "ws", "active": 3, "occupied": [1, 3], "urgent": [], "colors": {}, "names": {}}
    assert "dispatch workspace 3" in dispatched
    assert "dispatch movetoworkspacesilent 1" in dispatched


def test_link_up_reports_current_mute_state(monkeypatch, tmp_path):
    from keymakerd import volume as vol
    from keymakerd.__main__ import Config, Supervisor

    async def fake_status():
        return (0.4, True)

    async def scenario():
        monkeypatch.setattr(vol, "status", fake_status)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        await sup._on_link_up()
        return sent

    sent = asyncio.run(scenario())
    flags = next(m for m in sent if m["t"] == "flags")
    assert flags["muted"] is True


def test_volume_failure_still_sends_flags(monkeypatch, tmp_path):
    from keymakerd import volume as vol
    from keymakerd.__main__ import Config, Supervisor

    async def boom(*a):
        raise OSError("wpctl missing")

    async def scenario():
        monkeypatch.setattr(vol, "toggle_mute", boom)
        monkeypatch.setattr(vol, "status", boom)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        await sup._volume(0, True)
        return sent

    sent = asyncio.run(scenario())
    assert any(m["t"] == "flags" for m in sent)


def _quiet_ledger(monkeypatch):
    """Keep _poll_ledger off the real tmux server during ctx tests."""
    from keymakerd import tmux as tmuxmod

    async def none(*a, **k):
        return None

    monkeypatch.setattr(tmuxmod, "list_bells", none)
    monkeypatch.setattr(tmuxmod, "list_claude_panes", none)


def test_context_watcher_state_shaped_emission(monkeypatch, tmp_path):
    import keymakerd.__main__ as main_mod
    from keymakerd import tmux as tmuxmod

    async def fake_list(session):
        return [{"i": 1, "name": "vim", "active": True, "bell": False}]

    async def scenario():
        monkeypatch.setattr(main_mod, "CTX_POLL_S", 0.05)
        monkeypatch.setattr(tmuxmod, "list_windows", fake_list)
        _quiet_ledger(monkeypatch)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        # ws- window on the active workspace -- focus is irrelevant now
        sup.state.fg = {1: {"addr": "0xb", "cls": "ws-mirepoix"}}
        task = asyncio.create_task(sup._context())
        await asyncio.sleep(0.2)
        sup.state.active = 2                        # workspace with no ws-
        await asyncio.sleep(0.15)
        task.cancel()
        return sent

    sent = asyncio.run(scenario())
    ctx = [m for m in sent if m["t"] == "ctx"]
    tmux_msgs = [c for c in ctx if c["mode"] == "tmux"]
    assert len(tmux_msgs) == 1                      # state-shaped: no re-send
    assert tmux_msgs[0]["session"] == "mirepoix"
    assert tmux_msgs[0]["items"][0]["name"] == "vim"
    assert tmux_msgs[0]["addr"] == "0xb"            # tap needs the window address
    assert ctx[-1]["mode"] == "none"


def test_context_watcher_filters_slots_and_degrades(monkeypatch, tmp_path):
    import keymakerd.__main__ as main_mod
    from keymakerd import tmux as tmuxmod

    async def fake_list(session):
        return [{"i": i, "name": f"w{i}", "active": i == 1, "bell": False}
                for i in (1, 2, 7)]                 # 7 must be filtered out

    async def fake_list_fail(session):
        return None

    async def scenario(lister):
        monkeypatch.setattr(main_mod, "CTX_POLL_S", 0.05)
        monkeypatch.setattr(tmuxmod, "list_windows", lister)
        _quiet_ledger(monkeypatch)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.fg = {1: {"addr": "0xa", "cls": "ws-x"}}
        task = asyncio.create_task(sup._context())
        await asyncio.sleep(0.2)
        task.cancel()
        return [m for m in sent if m["t"] == "ctx"]

    ctx = asyncio.run(scenario(fake_list))
    assert [it["i"] for it in ctx[0]["items"]] == [1, 2]
    ctx = asyncio.run(scenario(fake_list_fail))     # tmux failure → mode none
    assert ctx == []                                # none == initial state: no emission


def test_context_watcher_survives_lister_exception(monkeypatch, tmp_path):
    import keymakerd.__main__ as main_mod
    from keymakerd import tmux as tmuxmod

    async def boom(session):
        raise RuntimeError("contract violation")

    async def scenario():
        monkeypatch.setattr(main_mod, "CTX_POLL_S", 0.05)
        monkeypatch.setattr(tmuxmod, "list_windows", boom)
        _quiet_ledger(monkeypatch)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.fg = {1: {"addr": "0xa", "cls": "ws-x"}}
        task = asyncio.create_task(sup._context())
        await asyncio.sleep(0.2)
        alive = not task.done()
        task.cancel()
        return alive, [m for m in sent if m["t"] == "ctx"]

    alive, ctx = asyncio.run(scenario())
    assert alive                      # the watcher survived the raise
    assert ctx == []                  # degraded to none == initial state, no emission


def test_context_watcher_falls_back_to_workspace_label_session(monkeypatch, tmp_path):
    # Rename case: class frozen at ws-macropad, session now "keymaker".
    import keymakerd.__main__ as main_mod
    from keymakerd import tmux as tmuxmod

    async def lister(session):
        if session == "keymaker":
            return [{"i": 1, "name": "vim", "active": True, "bell": False}]
        return None

    async def scenario():
        monkeypatch.setattr(main_mod, "CTX_POLL_S", 0.05)
        monkeypatch.setattr(tmuxmod, "list_windows", lister)
        _quiet_ledger(monkeypatch)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.fg = {3: {"addr": "0xm", "cls": "ws-macropad"}}
        sup.state.active = 3
        sup.state.names = {"3": "keymaker"}
        task = asyncio.create_task(sup._context())
        await asyncio.sleep(0.2)
        task.cancel()
        return [m for m in sent if m["t"] == "ctx"]

    ctx = asyncio.run(scenario())
    assert ctx and ctx[0]["mode"] == "tmux"
    assert ctx[0]["session"] == "keymaker"
    assert ctx[0]["items"][0]["name"] == "vim"


def test_bottom_half_key_selects_tmux_window(monkeypatch, tmp_path):
    from keymakerd import tmux as tmuxmod
    selected = []

    async def fake_select(session, i):
        selected.append((session, i))
        return True

    async def scenario():
        monkeypatch.setattr(tmuxmod, "select_window", fake_select)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sup.link = type("L", (), {"send": staticmethod(lambda m: True)})()
        sup.ctx = {"t": "ctx", "mode": "tmux", "session": "mirepoix", "items": [], "addr": None}
        sup._on_pad_msg({"t": "key", "n": 6, "act": "tap"})    # slot 1
        sup._on_pad_msg({"t": "key", "n": 11, "act": "tap"})   # slot 6
        sup._on_pad_msg({"t": "key", "n": 7, "act": "hold"})   # reserved: no-op
        sup.ctx = {"t": "ctx", "mode": "none", "session": None, "items": [], "addr": None}
        sup._on_pad_msg({"t": "key", "n": 8, "act": "tap"})    # mode none: ignored
        sup.ctx = {"t": "ctx", "mode": "tmux", "session": "mirepoix", "items": [], "addr": None}
        sup._on_pad_msg({"t": "key", "n": 13, "act": "tap"})   # out of range: ignored
        await asyncio.sleep(0.05)
        return selected

    assert asyncio.run(scenario()) == [("mirepoix", 1), ("mirepoix", 6)]


def test_tap_focuses_foot_window_first_when_unfocused(monkeypatch, tmp_path):
    from keymakerd import tmux as tmuxmod
    calls = []

    async def fake_select(session, i):
        calls.append(("select", session, i))
        return True

    async def fake_dispatch(cmd):
        calls.append(("hypr", cmd))

    async def scenario():
        monkeypatch.setattr(tmuxmod, "select_window", fake_select)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sup.link = type("L", (), {"send": staticmethod(lambda m: True)})()
        sup._dispatch = fake_dispatch
        sup.ctx = {"t": "ctx", "mode": "tmux", "session": "oracle",
                   "items": [], "addr": "0xfeet"}
        sup.state.addr = "0xffox"                 # firefox holds focus
        sup._on_pad_msg({"t": "key", "n": 6, "act": "tap"})
        await asyncio.sleep(0.05)
        first = list(calls)
        calls.clear()
        sup.state.addr = "0xfeet"                 # foot already focused
        sup._on_pad_msg({"t": "key", "n": 7, "act": "tap"})
        await asyncio.sleep(0.05)
        return first, list(calls)

    unfocused, focused = asyncio.run(scenario())
    # unfocused: focus the foot window FIRST, then select the tmux window
    assert unfocused == [("hypr", "dispatch focuswindow address:0xfeet"),
                         ("select", "oracle", 1)]
    assert focused == [("select", "oracle", 2)]   # no focus hop when already there


def test_ledger_emission_is_state_shaped_sorted_and_capped(monkeypatch, tmp_path):
    from keymakerd import tmux as tmuxmod
    from keymakerd.__main__ import LEDGER_MAX_CLAUDES

    claudes = [{"s": "a", "i": 1, "busy": True, "title": "t1"},
               {"s": "b", "i": 1, "busy": False, "title": "t2"},
               {"s": "c", "i": 1, "busy": True, "title": "t3"},
               {"s": "d", "i": 1, "busy": False, "title": "t4"},
               {"s": "e", "i": 1, "busy": True, "title": "t5"}]
    bells = [{"s": "b", "i": 3}]

    async def fake_bells(*a, **k):
        return bells

    async def fake_panes(*a, **k):
        return claudes

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_bells", fake_bells)
        monkeypatch.setattr(tmuxmod, "list_claude_panes", fake_panes)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        await sup._poll_ledger()
        await sup._poll_ledger()                  # unchanged: no re-send
        return sent

    sent = asyncio.run(scenario())
    ledgers = [m for m in sent if m["t"] == "ledger"]
    assert len(ledgers) == 1                      # state-shaped
    got = ledgers[0]["claudes"]
    assert len(got) == LEDGER_MAX_CLAUDES         # capped for the 1024B line limit
    # stable sort: the two waiting entries in arrival order, then busy; e shed
    assert [c["s"] for c in got] == ["b", "d", "a", "c"]
    assert ledgers[0]["bells"] == bells


def test_ledger_msg_sheds_entries_to_fit_line_limit():
    from keymakerd.__main__ import _ledger_msg, LEDGER_MAX_BYTES
    import km_proto
    # quote-heavy titles JSON-escape to 2 bytes/char: worst case measured
    # 1313B before the size check existed (review 2026-08-16)
    claudes = [{"s": "s" * 20, "i": 1, "busy": False, "title": '\"' * 20}
               for _ in range(4)]
    bells = [{"s": "s" * 20, "i": 1} for _ in range(6)]
    msg = _ledger_msg(claudes, bells)
    assert len(km_proto.encode(msg)) <= LEDGER_MAX_BYTES
    assert msg["bells"]                            # sheds claudes before bells


def test_link_up_snapshot_includes_ledger(monkeypatch, tmp_path):
    async def scenario():
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        await sup._on_link_up()
        return sent

    sent = asyncio.run(scenario())
    assert {"t": "ledger", "claudes": [], "bells": []} in sent


class TestCoachMessages:
    # NOTE: Config's field defaults (env lookups) evaluate at import time,
    # so tests pass state_dir explicitly — monkeypatching the env after
    # import would silently do nothing.
    def test_coach_session_persists_and_acks_with_state(self, tmp_path):
        cfg = Config(state_dir=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()

        async def go():
            sup._on_pad_msg({"t": "coach", "session": {
                "stage": 1, "bpm": 95, "swing": None, "greens": 16,
                "ambers": 0, "reds": 0, "misses": 0, "strays": 0,
                "score": 1.0, "duration_ms": 40000}})
            await asyncio.gather(*sup._tasks)
        asyncio.run(go())

        assert len(sup.coach.load()) == 1
        states = [m for m in sent if m.get("t") == "coach"]
        assert states and states[-1]["stages"]["1"]["best"] == 1.0

    def test_coach_ignores_garbage_session(self, tmp_path):
        sup = Supervisor(Config(state_dir=tmp_path))
        sup.link = type("L", (), {"send": staticmethod(lambda m: True)})()

        async def go():
            sup._on_pad_msg({"t": "coach", "session": "not-a-dict"})
            sup._on_pad_msg({"t": "coach"})
            await asyncio.gather(*sup._tasks)
        asyncio.run(go())
        assert sup.coach.load() == []

    def test_coach_rejects_invalid_session_but_still_acks(self, tmp_path):
        sup = Supervisor(Config(state_dir=tmp_path))
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()

        async def go():
            sup._on_pad_msg({"t": "coach", "session": {
                "stage": None, "score": 0.9, "duration_ms": 40000}})
            await asyncio.gather(*sup._tasks)
        asyncio.run(go())

        assert sup.coach.load() == []
        states = [m for m in sent if m.get("t") == "coach"]
        assert states and states[-1]["unlocked"] == 1
