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
    assert ws == {"t": "ws", "active": 3, "occupied": [1, 3], "urgent": [], "colors": {}}
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
