"""keymakerd: supervises serial link, Hyprland stream, theme watcher, volume."""
import asyncio
import json
import os

import km_proto
from dataclasses import dataclass
from pathlib import Path

from . import hyprland, tmux, volume
from .coach_store import CoachStore
from .serial_link import SerialLink
from .theme import ThemeWatcher

PING_S = 5.0
DEBOUNCE_S = 0.1
CTX_POLL_S = 1.0


def _ctx_none():
    return {"t": "ctx", "mode": "none", "session": None, "items": [], "addr": None}


def _ledger_none():
    return {"t": "ledger", "claudes": [], "bells": []}


# LineCodec discards lines over 1024 bytes WHOLE -- an uncapped ledger would
# render fine on a quiet bench and then silently show a STALE ledger on the
# busiest day (state-shaped protocol: nothing retransmits until the next
# change), which is exactly when it matters. Field caps live in tmux.py
# (_TITLE_MAX/_SESSION_MAX); count caps here; and because JSON escaping can
# still inflate quote-heavy titles (review 2026-08-16 measured 1313 B worst
# case pre-fix), the encoded size is CHECKED, shedding busy-last entries until
# it fits. Waiting Claudes sort first so the shedding order is least-interesting.
LEDGER_MAX_CLAUDES = 4
LEDGER_MAX_BELLS = 6
LEDGER_MAX_BYTES = 1000     # headroom under LineCodec's 1024


def _ledger_msg(claudes, bells):
    claudes = sorted(claudes, key=lambda c: c["busy"])[:LEDGER_MAX_CLAUDES]
    bells = bells[:LEDGER_MAX_BELLS]
    while True:
        msg = {"t": "ledger", "claudes": claudes, "bells": bells}
        if len(km_proto.encode(msg)) <= LEDGER_MAX_BYTES:
            return msg
        if claudes:
            claudes = claudes[:-1]
        elif bells:
            bells = bells[:-1]
        else:
            return msg


def _session_name(label):
    """footguard's fg_session_name, ported: tmux/systemd-safe session name."""
    s = label
    for ch in ".:/":
        s = s.replace(ch, "-")
    s = "-".join(s.split())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


@dataclass
class Config:
    device: str = os.environ.get("KEYMAKER_DEVICE", "/dev/keymaker-data")
    runtime_dir: Path = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
    home: Path = Path.home()
    state_dir: Path = Path(os.environ.get("KEYMAKER_STATE_DIR")
                           or Path.home() / ".local/state/keymaker")


class Supervisor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = hyprland.HyprState()
        self.muted = False
        self.palette = None
        self.ctx = _ctx_none()
        self.ledger = _ledger_none()
        self.coach = CoachStore(cfg.state_dir / "coach.json")
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
        self.link.send(self.ledger)
        try:
            _, self.muted = await volume.status()
        except (OSError, ValueError, IndexError):
            pass
        self.link.send(self._flags_msg())
        self.link.send(self.coach.state_msg())

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
                if verb != "workspace":                   # holds are rare and destructive; log for forensics
                    print(f"keymakerd: hold key {n} -> {verb} {n + 1}", flush=True)
                self._spawn(self._dispatch(f"dispatch {verb} {n + 1}"), "dispatch")
            elif 6 <= n <= 11 and msg.get("act") == "tap" and self.ctx["mode"] == "tmux":
                self._spawn(self._activate_window(dict(self.ctx), n - 5), "tmux-activate")
        elif t == "dial":
            self._spawn(self._volume(int(msg.get("d", 0)), False), "volume")
        elif t == "click":
            self._spawn(self._volume(0, True), "volume")
        elif t == "coach":
            self._spawn(self._coach_session(msg.get("session")), "coach")

    async def _dispatch(self, cmd):
        if self._instance is not None:
            try:
                await hyprland.request(self._instance, cmd)
            except OSError:
                self._instance = None

    async def _activate_window(self, ctx, i):
        """Tap on a bottom key: focus the workspace's foot window if it isn't
        focused (the deck is workspace-aware, not focus-gated), then select the
        tmux window. ctx is a snapshot taken at tap time so a poll racing this
        coroutine cannot swap the target under it."""
        addr = ctx.get("addr")
        if addr and addr != self.state.addr:
            await self._dispatch(f"dispatch focuswindow address:{addr}")
        if not await tmux.select_window(ctx["session"], i):
            print(f"keymakerd: select-window {ctx['session']}:{i} failed", flush=True)

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

    async def _coach_session(self, session):
        if not isinstance(session, dict):
            return
        try:
            self.coach.append(session)
        except Exception as e:
            print(f"keymakerd: coach store failed: {e!r}", flush=True)
        self.link.send(self.coach.state_msg())

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
        # The deck is WORKSPACE-aware, not focus-gated: keys 6-11 track the
        # footguard window living on the active workspace whether or not it holds
        # focus, so the bottom half stays lit while you're in the browser next to
        # it. Workspaces with no footguard window still cost nothing (no poll).
        while True:
            await asyncio.sleep(CTX_POLL_S)
            client = self.state.fg.get(self.state.active)
            new = _ctx_none()
            if client is not None:
                candidates = [client["cls"].removeprefix("footguard-")]
                label = self.state.names.get(str(self.state.active))
                if label:
                    fallback = _session_name(label)
                    if fallback and fallback not in candidates:
                        candidates.append(fallback)
                try:
                    for session in candidates:
                        items = await tmux.list_windows(session)
                        if items is not None:
                            new = {"t": "ctx", "mode": "tmux", "session": session,
                                   "items": [w for w in items if 1 <= w["i"] <= 6],
                                   "addr": client["addr"]}
                            break
                except Exception as e:
                    print(f"keymakerd: ctx poll failed: {e!r}", flush=True)
            if new != self.ctx:
                self.ctx = new
                self.link.send(new)
            await self._poll_ledger()

    async def _poll_ledger(self):
        # Global on purpose -- the ledger is the "what's waiting on me" surface
        # and must keep reporting while focus (or the whole screen, via hyprlock)
        # is elsewhere. The pad is a display hyprlock has no jurisdiction over.
        try:
            bells = await tmux.list_bells()
            claudes = await tmux.list_claude_panes()
        except Exception as e:
            print(f"keymakerd: ledger poll failed: {e!r}", flush=True)
            return
        new = _ledger_msg(claudes or [], bells or [])
        if new != self.ledger:
            self.ledger = new
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
