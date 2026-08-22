"""keymakerd: supervises serial link, Hyprland stream, theme watcher, volume."""
import asyncio
import json
import os

import km_deck
import km_proto
from dataclasses import dataclass
from pathlib import Path

from . import hyprland, ledtest, tmux, volume
from .coach_store import CoachStore
from .deck_store import DeckStore
from .serial_link import SerialLink
from .theme import ThemeWatcher, resolve_theme_dir

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
        self.deck_store = DeckStore(cfg.state_dir / "deck-slots.json")
        self.deck = km_deck.Deck(self.deck_store.load())
        self.knob_mode = "vol"      # short-press toggles; rotation is volume first
        self.deck_page = 0
        self.deck_msg = None
        # Cache of the deck poll's tmux windows, keyed by id ("tmux:@N"), and of
        # session -> ws-* client address -- both needed at tap time to focus a
        # window, but only cheaply available inside the poll that already fetched
        # them. Empty until the first deck poll lands, so an early tap is a no-op
        # rather than a KeyError.
        self._deck_twins = {}
        self._deck_ws_addr = {}
        # Last (colors, focused, bells) _poll_deck computed, so a knob gesture can
        # re-render the deck message SYNCHRONOUSLY instead of waiting up to
        # CTX_POLL_S for the next poll -- a knob that takes a second to respond
        # reads as a broken control. None until the first poll lands.
        self._deck_render_args = None
        self.link = SerialLink(cfg.device, on_msg=self._on_pad_msg,
                               on_up=self._on_link_up, on_down=self._on_link_down)
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
        # The deck is the switchboard itself: a reconnecting pad (USB reset, a
        # firmware deploy, the app-menu round trip in framework.py, which also
        # re-sends `hello`) otherwise starts from its empty default deck and
        # stays that way until some UNRELATED window opens/closes/renames --
        # _poll_deck's dedup guard stays silent because the computed message
        # hasn't changed, even though the pad has never seen it. None only
        # before the first poll has ever landed; nothing to send yet then.
        if self.deck_msg is not None:
            self.link.send(self.deck_msg)
        try:
            _, self.muted = await volume.status()
        except (OSError, ValueError, IndexError):
            pass
        self.link.send(self._flags_msg())
        self.link.send(self.coach.state_msg())

    def _on_link_down(self):
        # Force the next poll to re-emit even if nothing about the windows has
        # changed -- see _on_link_up's resend above for why a dedup-suppressed
        # deck otherwise leaves a reconnected pad blank.
        self.deck_msg = None

    async def _on_palette(self, pal):
        self.palette = pal
        self.link.send(pal)
        # Re-resolved here since this callback already fires on every theme change
        # (ThemeWatcher only calls it when colors.toml's mtime moves). light.mode is
        # Omarchy's marker file, a sibling of colors.toml in the same theme dir.
        #
        # NOTE: since colorhash (2026-08-21) this no longer affects workspace colors —
        # ws_color reads palette.json's `led` surface, which is a physical WS2812
        # rendering with no theme ground to contrast against. self.state.light still
        # drives the rest of the pad's rendering, so the watch stays.
        theme_dir = resolve_theme_dir(self.cfg.home)
        light = bool(theme_dir) and (theme_dir / "light.mode").exists()
        if light != self.state.light:
            self.state.light = light
            self._refresh_wanted.set()

    # ---- deck -------------------------------------------------------
    def save_deck(self):
        self.deck_store.save(self.deck.slots)

    def _deck_msg(self, colors, focused, bells):
        # Clamp rather than trust: windows closing can shrink the page count out
        # from under a page the knob already selected.
        self.deck_page = min(self.deck_page, self.deck.page_count() - 1)
        return self.deck.message(self.deck_page, self.knob_mode, colors,
                                 focused=focused, bells=bells)

    def _resend_deck(self):
        """Re-render and (if changed) send the deck message immediately, from the
        last (colors, focused, bells) _poll_deck computed. Called by the knob
        handlers so a mode toggle or a page change doesn't wait up to
        CTX_POLL_S for the next poll to reach the pad -- a knob that takes a
        second to respond reads as a broken control. A no-op before the first
        poll has landed (nothing to render yet, and no live windows to lose)."""
        if self._deck_render_args is None:
            return
        colors, focused, bells = self._deck_render_args
        msg = self._deck_msg(colors, focused, bells)
        if msg != self.deck_msg:
            self.deck_msg = msg
            self.link.send(msg)

    def on_knob_press(self):
        self.knob_mode = "page" if self.knob_mode == "vol" else "vol"
        self._resend_deck()

    def on_knob_turn(self, delta):
        if self.knob_mode != "page":
            return                      # volume is handled by the existing path
        pages = self.deck.page_count()
        self.deck_page = (self.deck_page + delta + pages) % pages
        self._resend_deck()

    def on_tap(self, slot):
        """Key press on the current page. Returns what happened, for tests."""
        if not 0 <= slot < km_deck.SLOTS_PER_PAGE:
            return None          # the pad has 12 keys; anything else is a bad frame
        gslot = self.deck_page * km_deck.SLOTS_PER_PAGE + slot
        if self.deck.dismiss(gslot):
            self.save_deck()
            return "dismissed"
        for wid, s in self.deck.slots.items():
            if s == gslot:
                return wid              # caller focuses it; see _focus_deck_window
        return None

    def _focused_window_id(self, twins):
        """The id (see km_deck) of the window that currently holds focus, or None.

        A tmux window counts as focused when its session is the ws-* client
        living on the active workspace AND tmux itself reports it active --
        matching deck_windows' notion of "the window you'd land in" rather than
        Hyprland's, since the ws-* terminal can hold Hyprland focus while any of
        its tmux windows is the one actually on screen. Falls back to the plain
        Hyprland-focused client id (covers sessionless terminals and the case
        where the focused client isn't a ws-* terminal at all).
        """
        client = self.state.fg.get(self.state.active)
        if client is not None:
            session = client["cls"].removeprefix("ws-")
            for w in twins:
                if w["s"] == session and w["active"]:
                    return w["id"]
        if self.state.addr:
            return "hypr:" + self.state.addr
        return None

    async def _focus_deck_window(self, wid):
        """Focus a window returned by on_tap. Mirrors _activate_window's shape:
        bring the right Hyprland client forward first (a no-op if it's already
        focused), then select the tmux window inside it."""
        if wid.startswith("tmux:"):
            twin = self._deck_twins.get(wid)
            if twin is None:
                return
            addr = self._deck_ws_addr.get(twin["s"])
            if addr and addr != self.state.addr:
                await self._dispatch(f"dispatch focuswindow address:{addr}")
            if not await tmux.select_window(twin["s"], twin["i"]):
                print(f"keymakerd: deck select-window {twin['s']}:{twin['i']} failed",
                      flush=True)
        elif wid.startswith("hypr:"):
            addr = wid.removeprefix("hypr:")
            if addr != self.state.addr:
                await self._dispatch(f"dispatch focuswindow address:{addr}")

    async def _on_tap(self, slot):
        result = self.on_tap(slot)
        if result and result != "dismissed":
            await self._focus_deck_window(result)

    # ---- inbound from pad -----------------------------------------
    def _on_pad_msg(self, msg):
        t = msg.get("t")
        if t == "hello":
            self._spawn(self._on_link_up(), "link-up")
        elif t == "key":
            n = int(msg.get("n", 0))
            if msg.get("act") == "tap":                   # hold is reserved: no-op
                self._spawn(self._on_tap(n), "deck-tap")
        elif t == "dial":
            d = int(msg.get("d", 0))
            if self.knob_mode == "page":
                self.on_knob_turn(d)
            else:
                self._spawn(self._volume(d, False), "volume")
        elif t == "click":
            self.on_knob_press()
        elif t == "coach":
            self._spawn(self._coach_session(msg.get("session")), "coach")

    async def _dispatch(self, cmd):
        # Swallow transient IPC failures WITHOUT clearing _instance:
        # _hypr_events owns that handle (it rediscovers after its socket2
        # connection dies, which is what a real Hyprland restart looks like).
        # Clearing it here permanently disabled the refresher, since nothing
        # else could ever set it again (live incident 2026-08-18).
        if self._instance is not None:
            try:
                await hyprland.request(self._instance, cmd)
            except OSError as e:
                print(f"keymakerd: dispatch failed ({e!r}): {cmd}", flush=True)

    # `_activate_window` (the old ctx-based bottom-half tap handler) is retired:
    # `_focus_deck_window` below does the same job -- focus the client, then
    # select-window -- against deck slots instead of ctx's fixed 6-11 range.

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
        # `ctx` here is the OLD split-deck context message, superseded by `deck`
        # (see docs/superpowers/specs/2026-08-22-macropad-switchboard-design.md).
        # Left running rather than deleted: cockpit.py (Task 9) hasn't switched to
        # `deck` rendering yet, and untangling this from `_poll_ledger` below is a
        # separate cleanup once nothing consumes it, not this task's job.
        while True:
            await asyncio.sleep(CTX_POLL_S)
            client = self.state.fg.get(self.state.active)
            new = _ctx_none()
            if client is not None:
                candidates = [client["cls"].removeprefix("ws-")]
                label = self.state.names.get(str(self.state.active))
                if label:
                    fallback = hyprland.session_name(label)
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
            await self._poll_deck()
            await self._poll_ledger()

    async def _poll_deck(self):
        try:
            twins = await tmux.list_deck_windows()
            if twins is None:
                return
            self._deck_twins = {w["id"]: w for w in twins}
            self._deck_ws_addr = {
                str(c.get("class", ""))[3:]: str(c.get("address", ""))
                for c in self.state.clients if str(c.get("class", "")).startswith("ws-")
            }
            wins = hyprland.deck_windows(twins, self.state.clients, self.state.workspaces)
            before = dict(self.deck.slots)
            self.deck.update(wins)
            if self.deck.slots != before:
                self.save_deck()
            colors = hyprland.deck_colors(wins)
            bells = hyprland.deck_bells(twins, self.state._urgent_addrs, self.state.clients)
            focused = self._focused_window_id(twins)
            self._deck_render_args = (colors, focused, bells)
            msg = self._deck_msg(colors, focused, bells)
            if msg != self.deck_msg:
                self.deck_msg = msg
                self.link.send(msg)
        except Exception as e:
            print(f"keymakerd: deck poll failed: {e!r}", flush=True)

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
        # ledtest: spike-grade debug bridge for palette eyeballing. Always via
        # cfg.state_dir so test_supervisor.py's tmp_path stays hermetic; the
        # watcher idles harmlessly when the spool never appears.
        await asyncio.gather(self.link.run(), self._hypr_events(),
                             self._refresher(), self._pinger(),
                             self._context(), theme.run(),
                             ledtest.watch(str(self.cfg.state_dir / "ledtest.json"),
                                           self.link.send))


def main():
    asyncio.run(Supervisor(Config()).run())


if __name__ == "__main__":
    main()
