"""keymakerd: supervises serial link, Hyprland stream, theme watcher, volume."""
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import hyprland, tmux, volume
from .serial_link import SerialLink
from .theme import ThemeWatcher

PING_S = 5.0
DEBOUNCE_S = 0.1
CTX_POLL_S = 1.0


def _ctx_none():
    return {"t": "ctx", "mode": "none", "session": None, "items": []}


@dataclass
class Config:
    device: str = os.environ.get("KEYMAKER_DEVICE", "/dev/keymaker-data")
    runtime_dir: Path = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
    home: Path = Path.home()


class Supervisor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = hyprland.HyprState()
        self.muted = False
        self.palette = None
        self.ctx = _ctx_none()
        self.link = SerialLink(cfg.device, on_msg=self._on_pad_msg, on_up=self._on_link_up)
        self._refresh_wanted = asyncio.Event()
        self._instance = None
        self._tasks = set()

    def _spawn(self, coro, what):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def _done(t):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                print(f"keymakerd: {what} failed: {t.exception()!r}", flush=True)

        task.add_done_callback(_done)

    # ---- outbound -------------------------------------------------
    def _flags_msg(self):
        return {"t": "flags", "submap": self.state.submap,
                "screencast": self.state.screencast, "muted": self.muted}

    async def _on_link_up(self):
        self.link.send({"t": "hello", "host": "keymakerd", "proto": 1})
        if self.palette:
            self.link.send(self.palette)
        for m in self.state.snapshot():
            self.link.send(m)
        self.link.send(self.ctx)
        try:
            _, self.muted = await volume.status()
        except (OSError, ValueError, IndexError):
            pass
        self.link.send(self._flags_msg())

    async def _on_palette(self, pal):
        self.palette = pal
        self.link.send(pal)

    # ---- inbound from pad -----------------------------------------
    def _on_pad_msg(self, msg):
        t = msg.get("t")
        if t == "hello":
            self._spawn(self._on_link_up(), "link-up")
        elif t == "key":
            n = int(msg.get("n", 0))
            if n < 6:                                     # top half: workspaces 1-6
                verb = "movetoworkspacesilent" if msg.get("act") == "hold" else "workspace"
                self._spawn(self._dispatch(f"dispatch {verb} {n + 1}"), "dispatch")
            elif msg.get("act") == "tap" and self.ctx["mode"] == "tmux":
                self._spawn(self._select_window(self.ctx["session"], n - 5), "tmux-select")
        elif t == "dial":
            self._spawn(self._volume(int(msg.get("d", 0)), False), "volume")
        elif t == "click":
            self._spawn(self._volume(0, True), "volume")

    async def _dispatch(self, cmd):
        if self._instance is not None:
            try:
                await hyprland.request(self._instance, cmd)
            except OSError:
                self._instance = None

    async def _select_window(self, session, i):
        if not await tmux.select_window(session, i):
            print(f"keymakerd: select-window {session}:{i} failed", flush=True)

    async def _volume(self, direction, toggle):
        try:
            if toggle:
                await volume.toggle_mute()
            elif direction:
                await volume.step(direction)
            _, self.muted = await volume.status()
        except (OSError, ValueError, IndexError):
            pass
        self.link.send(self._flags_msg())

    # ---- hyprland side --------------------------------------------
    async def _hypr_events(self):
        while True:
            self._instance = hyprland.find_instance_dir(self.cfg.runtime_dir)
            if self._instance is None:
                await asyncio.sleep(2)
                continue
            try:
                reader, _ = await asyncio.open_unix_connection(
                    str(self._instance / ".socket2.sock"))
                self._refresh_wanted.set()
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    ev = hyprland.parse_event(line.decode(errors="replace").rstrip("\n"))
                    if ev is None:
                        continue
                    needs_refresh, flags_changed = self.state.handle_event(*ev)
                    if needs_refresh:
                        self._refresh_wanted.set()
                    if flags_changed:
                        self.link.send(self._flags_msg())
            except OSError:
                pass
            await asyncio.sleep(2)   # hyprland restarting; rediscover

    async def _refresher(self):
        while True:
            await self._refresh_wanted.wait()
            await asyncio.sleep(DEBOUNCE_S)               # coalesce bursts
            self._refresh_wanted.clear()
            if self._instance is None:
                continue
            try:
                ws = json.loads(await hyprland.request(self._instance, "j/workspaces"))
                aw = json.loads(await hyprland.request(self._instance, "j/activeworkspace"))
                win_raw = await hyprland.request(self._instance, "j/activewindow")
                win = json.loads(win_raw) if win_raw.strip() not in (b"", b"{}") else None
                clients = json.loads(await hyprland.request(self._instance, "j/clients"))
            except (OSError, ValueError):
                continue
            for m in self.state.refresh(ws, aw, win, clients):
                self.link.send(m)

    async def _pinger(self):
        while True:
            await asyncio.sleep(PING_S)
            self.link.send({"t": "ping"})

    async def _context(self):
        while True:
            await asyncio.sleep(CTX_POLL_S)
            cls = self.state.cls
            new = _ctx_none()
            if cls.startswith("footguard-"):
                session = cls.removeprefix("footguard-")
                try:
                    items = await tmux.list_windows(session)
                except Exception as e:
                    print(f"keymakerd: ctx poll failed: {e!r}", flush=True)
                    items = None
                if items is not None:
                    new = {"t": "ctx", "mode": "tmux", "session": session,
                           "items": [w for w in items if 1 <= w["i"] <= 6]}
            if new != self.ctx:
                self.ctx = new
                self.link.send(new)

    async def run(self):
        theme = ThemeWatcher(self.cfg.home, self._on_palette)
        await asyncio.gather(self.link.run(), self._hypr_events(),
                             self._refresher(), self._pinger(),
                             self._context(), theme.run())


def main():
    asyncio.run(Supervisor(Config()).run())


if __name__ == "__main__":
    main()
