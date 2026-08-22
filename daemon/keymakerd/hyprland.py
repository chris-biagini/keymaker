"""Hyprland IPC: instance discovery, one-shot requests, event stream state."""
import asyncio
import json
import unicodedata
from pathlib import Path

# colorhash replaced the cksum%7 / Okabe-Ito system on 2026-08-21. The palette is
# DATA now — the same ~/.config/colorhash/palette.json the bash side reads
# (~/oracle/scripts/workspace-identity-lib). Pad and bar agree because they read one
# file, not because two hardcoded lists were kept in sync by hand.
#
# We take the `led` surface specifically. palette.json carries five renderings per
# cell and they are NOT interchangeable: `bg` is Petroff's published fill, `lightFg`
# and `darkFg` are re-lightened for text on a theme ground, and `led` is the one
# tuned for WS2812s. Using `bg` here would send a color that was never checked
# against the pad's LEDs.
PALETTE_FILE = Path.home() / ".config" / "colorhash" / "palette.json"

_LED_CACHE = None


def led_palette(path=None):
    """The `led` hex column of palette.json, or [] when it is absent/malformed.

    Empty means NO colors get sent, never a fallback set: a wrong-but-plausible
    palette on the pad is harder to notice than an unlit one.
    """
    global _LED_CACHE
    if path is None and _LED_CACHE is not None:
        return _LED_CACHE
    try:
        data = json.loads((path or PALETTE_FILE).read_text())
        pal = [str(c["led"]).lstrip("#").lower() for c in data["cells"]]
        if not all(len(h) == 6 for h in pal):
            pal = []
    except (OSError, ValueError, KeyError, TypeError):
        pal = []
    if path is None:
        _LED_CACHE = pal
    return pal


def fnv1a32(text):
    """FNV-1a 32-bit over UTF-8 bytes of the NFC-normalized string.

    The frozen colorhash contract: FNV1a32(UTF8(NFC(name))). Golden vector —
    "café" -> 0xa82b5049. Must agree byte-for-byte with wsid_hash in
    workspace-identity-lib and with lab.html's fnv1a, or pad and bar drift apart.
    """
    h = 0x811C9DC5
    for b in unicodedata.normalize("NFC", text).encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def session_name(label):
    """tmux/systemd-safe session name from a workspace label.
    Ported from wsid_session_name in ~/oracle/scripts/workspace-identity-lib —
    keep both in sync."""
    s = label
    for ch in ".:/":
        s = s.replace(ch, "-")
    s = "-".join(s.split())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def ws_color(name, light=False):
    """colorhash(sanitized session name) -> LED hex, or None when there is no color.

    Hashes the SANITIZED form so the bar (which hashes plain workspace names)
    and the pad/tmux side (which hashes the sanitized session name) agree even
    when a name needs sanitizing, e.g. workspace "wacky sax" -> session
    "wacky-sax".

    `light` is accepted and IGNORED. It selected between two theme-tuned palettes
    under the old system; colorhash's `led` surface is a physical rendering on
    WS2812s, which have no theme ground to contrast against. The parameter stays
    so callers (and _on_palette's theme-change refresh) need no edit, and so a
    future surface that IS theme-dependent has somewhere to land.
    """
    if not name or name.isdigit():
        return None
    pal = led_palette()
    if not pal:
        return None
    return pal[fnv1a32(session_name(name)) % len(pal)]


def ws_label(name):
    """Human label from a workspace name, or None for unnamed (bare-digit) names."""
    if not name:
        return None
    text = name.strip()
    if not text or text.isdigit():
        return None
    return text


REFRESH_EVENTS = {
    "workspace", "workspacev2", "focusedmon", "openwindow", "closewindow",
    "movewindow", "activewindow", "activewindowv2", "urgent", "fullscreen",
    "monitoradded", "monitorremoved", "renameworkspace",
}


def find_instance_dir(runtime):
    best = best_m = None
    for d in Path(runtime).glob("hypr/*"):
        try:
            if (d / ".socket.sock").exists():
                m = d.stat().st_mtime
                if best is None or m > best_m:
                    best, best_m = d, m
        except OSError:
            continue
    return best


async def request(instance_dir, cmd):
    reader, writer = await asyncio.open_unix_connection(str(instance_dir / ".socket.sock"))
    writer.write(cmd.encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    return data


def parse_event(line):
    if ">>" not in line:
        return None
    name, _, data = line.partition(">>")
    return name, data


class HyprState:
    def __init__(self):
        self.active = 1
        self.occupied = []
        self.urgent_ws = []
        self.colors = {}
        self.names = {}
        self.light = False
        self.cls = ""
        self.title = ""
        self.addr = ""
        self.fg = {}          # ws id -> {"addr", "cls"} best ws-* client
        self.submap = ""
        self.screencast = False
        self._urgent_addrs = set()
        # Raw IPC snapshots, retained for the deck poll (hyprland.deck_windows /
        # deck_bells need both). Initialised empty so a deck poll firing before
        # the first refresh cannot raise.
        self.clients = []
        self.workspaces = []

    def handle_event(self, name, data):
        """Returns (needs_refresh, flags_changed)."""
        if name == "submap":
            changed = data != self.submap
            self.submap = data
            return False, changed
        if name == "screencast":
            on = data.split(",")[0] == "1"
            changed = on != self.screencast
            self.screencast = on
            return False, changed
        # "bell" is the xdg-system-bell spelling of urgency: Hyprland >= 0.5x
        # emits it (and does NOT set client urgency or an "urgent" event) when a
        # terminal rings BEL via the system-bell protocol, which foot now uses.
        if name in ("urgent", "bell"):
            self._urgent_addrs.add(data.removeprefix("0x"))
            return True, False
        return name in REFRESH_EVENTS, False

    def refresh(self, workspaces, active_ws, active_win, clients):
        msgs = []
        self.clients = clients
        self.workspaces = workspaces
        active = active_ws.get("id", 1)
        occupied = sorted(w["id"] for w in workspaces if w.get("windows", 0) > 0)
        addr_ws = {
            str(c.get("address", "")).removeprefix("0x"): c.get("workspace", {}).get("id")
            for c in clients
        }
        self._urgent_addrs = {
            a for a in self._urgent_addrs
            if addr_ws.get(a) is not None and addr_ws[a] != active
        }
        urgent = sorted({addr_ws[a] for a in self._urgent_addrs})
        # Per-workspace ws-* client, for the workspace-aware bottom deck.
        # focusHistoryID 0 is the focused window, ascending = less recent, so the
        # lowest id per workspace is "the terminal you were most recently in there".
        # Daemon-internal (drives ctx polling and tap focus), never sent to the pad,
        # so it is deliberately NOT part of the changed-state comparison below.
        fg = {}
        for c in clients:
            if not str(c.get("class", "")).startswith("ws-"):
                continue
            ws_id = c.get("workspace", {}).get("id")
            if ws_id is None:
                continue
            hist = c.get("focusHistoryID", 1 << 30)
            if ws_id not in fg or hist < fg[ws_id][0]:
                fg[ws_id] = (hist, {"addr": str(c.get("address", "")),
                                    "cls": str(c.get("class", ""))})
        self.fg = {ws: entry for ws, (_, entry) in fg.items()}
        colors = {}
        for w in workspaces:
            c = ws_color(w.get("name"), light=self.light)
            if c is not None:
                colors[str(w["id"])] = c
        names = {}
        for w in workspaces:
            lbl = ws_label(w.get("name"))
            if lbl is not None:
                names[str(w["id"])] = lbl
        if (active, occupied, urgent, colors, names) != (
                self.active, self.occupied, self.urgent_ws, self.colors, self.names):
            self.active, self.occupied, self.urgent_ws, self.colors, self.names = (
                active, occupied, urgent, colors, names)
            msgs.append(self._ws_msg())
        cls = (active_win or {}).get("class", "")
        title = (active_win or {}).get("title", "")[:60]
        self.addr = str((active_win or {}).get("address", ""))
        if (cls, title) != (self.cls, self.title):
            self.cls, self.title = cls, title
            msgs.append(self._win_msg())
        return msgs

    def snapshot(self):
        return [self._ws_msg(), self._win_msg()]

    def _ws_msg(self):
        return {"t": "ws", "active": self.active, "occupied": self.occupied,
                "urgent": self.urgent_ws, "colors": self.colors,
                "names": self.names}

    def _win_msg(self):
        return {"t": "win", "cls": self.cls, "title": self.title}


# A key is for something that can ASK FOR YOU and that you can GO TO locally.
# Terminals qualify on both counts: a tmux window rings via window_bell_flag, a
# bare terminal rings via Hyprland's bell event (foot.ini [bell] urgent=yes).
# A browser does neither, and three of them would eat a quarter of the deck.
TERMINAL_CLASSES = ("foot", "footclient", "alacritty", "kitty", "ghostty")

# Unnamed (bare-digit) workspaces have no identity to hash, so they render white:
# achromatic reads as "no identity assigned", which is exactly true. This is a HUE
# substitute, not a brightness state -- see spec section 5.2.
NEUTRAL_COLOR = "ffffff"


def _is_terminal(cls):
    c = str(cls or "").lower()
    return c in TERMINAL_CLASSES or c.startswith("ws-")


def deck_windows(tmux_windows, clients):
    """Windows that earn a key, as [{id, ws, n}] for km_deck.Deck.update."""
    ws_of_session = {}
    used_addrs = set()
    for c in clients:
        cls = str(c.get("class", ""))
        if cls.startswith("ws-"):
            ws_of_session[cls[3:]] = str(c.get("workspace", {}).get("name", ""))
            used_addrs.add(str(c.get("address", "")))

    out = []
    for win in tmux_windows:
        ws = ws_of_session.get(win["s"])
        if ws is None:              # session with no local client: nothing to jump to
            continue
        out.append({"id": win["id"], "ws": ws,
                    "n": "%d %s" % (win["i"], win["n"])})

    for c in clients:
        addr = str(c.get("address", ""))
        if addr in used_addrs or not _is_terminal(c.get("class")):
            continue
        out.append({"id": "hypr:" + addr,
                    "ws": str(c.get("workspace", {}).get("name", "")),
                    "n": str(c.get("title", "") or c.get("class", ""))})
    return out


def deck_colors(windows):
    """{workspace name: led hex} for a deck window list, white where unnamed."""
    return {win["ws"]: (ws_color(win["ws"]) or NEUTRAL_COLOR) for win in windows}


def deck_bells(tmux_windows, urgent_addrs, clients):
    """Window ids with an unacked bell. Spec section 6.1.

    A single BEL inside a ws-* terminal fires BOTH channels: tmux sets
    window_bell_flag, and foot rings the system bell for the surrounding client,
    which Hyprland emits as `bell`. The tmux flag names the exact window; the
    Hyprland event names only the terminal. So where a session exists the tmux
    flag WINS and the Hyprland event is discarded -- otherwise one bell would
    light the right key and smear across every key of that session.
    """
    out = {w["id"] for w in tmux_windows if w.get("bell")}
    ws_addrs = {str(c.get("address", "")).removeprefix("0x")
                for c in clients if str(c.get("class", "")).startswith("ws-")}
    for addr in urgent_addrs:
        if addr in ws_addrs:
            continue
        out.add("hypr:0x" + addr)
    return out
