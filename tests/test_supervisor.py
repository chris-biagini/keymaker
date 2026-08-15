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
        cfg = Config(device=slave_path, runtime_dir=tmp_path, home=tmp_path)
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
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
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


def test_context_watcher_state_shaped_emission(monkeypatch, tmp_path):
    import keymakerd.__main__ as main_mod
    from keymakerd import tmux as tmuxmod

    async def fake_list(session):
        return [{"i": 1, "name": "vim", "active": True, "bell": False}]

    async def scenario():
        monkeypatch.setattr(main_mod, "CTX_POLL_S", 0.05)
        monkeypatch.setattr(tmuxmod, "list_windows", fake_list)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.cls = "footguard-mirepoix"
        task = asyncio.create_task(sup._context())
        await asyncio.sleep(0.2)
        sup.state.cls = "firefox"
        await asyncio.sleep(0.15)
        task.cancel()
        return sent

    sent = asyncio.run(scenario())
    ctx = [m for m in sent if m["t"] == "ctx"]
    tmux_msgs = [c for c in ctx if c["mode"] == "tmux"]
    assert len(tmux_msgs) == 1                      # state-shaped: no re-send
    assert tmux_msgs[0]["session"] == "mirepoix"
    assert tmux_msgs[0]["items"][0]["name"] == "vim"
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
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.cls = "footguard-x"
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
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.cls = "footguard-x"
        task = asyncio.create_task(sup._context())
        await asyncio.sleep(0.2)
        alive = not task.done()
        task.cancel()
        return alive, [m for m in sent if m["t"] == "ctx"]

    alive, ctx = asyncio.run(scenario())
    assert alive                      # the watcher survived the raise
    assert ctx == []                  # degraded to none == initial state, no emission


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
        sup.ctx = {"t": "ctx", "mode": "tmux", "session": "mirepoix", "items": []}
        sup._on_pad_msg({"t": "key", "n": 6, "act": "tap"})    # slot 1
        sup._on_pad_msg({"t": "key", "n": 11, "act": "tap"})   # slot 6
        sup._on_pad_msg({"t": "key", "n": 7, "act": "hold"})   # reserved: no-op
        sup.ctx = {"t": "ctx", "mode": "none", "session": None, "items": []}
        sup._on_pad_msg({"t": "key", "n": 8, "act": "tap"})    # mode none: ignored
        await asyncio.sleep(0.05)
        return selected

    assert asyncio.run(scenario()) == [("mirepoix", 1), ("mirepoix", 6)]
