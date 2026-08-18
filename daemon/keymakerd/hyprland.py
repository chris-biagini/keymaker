"""Hyprland IPC: instance discovery, one-shot requests, event stream state."""
import asyncio
from pathlib import Path

# 7-bin Okabe-Ito palettes, verbatim from ~/oracle/scripts/workspace-identity-lib
# (the bash home of the same constants — change both or neither).
PALETTE_DARK = ["f0a52d", "5ab4ff", "c3ffe1", "87875a", "4b8796", "e14b00", "ffd2f0"]
PALETTE_LIGHT = ["c36900", "0087e1", "002d1e", "3c3c00", "003cc3", "78694b", "4b003c"]

_CKSUM_TABLE = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = ((_c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if _c & 0x80000000 else (_c << 1) & 0xFFFFFFFF
    _CKSUM_TABLE.append(_c)


def posix_cksum(data):
    """POSIX cksum CRC (NOT zlib crc32): length bytes appended, final complement.
    Must match coreutils `cksum` exactly — it is the color-hash contract with
    the bash side (wsid_color in workspace-identity-lib)."""
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CKSUM_TABLE[((crc >> 24) ^ b) & 0xFF]
    length = len(data)
    while length:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CKSUM_TABLE[((crc >> 24) ^ (length & 0xFF)) & 0xFF]
        length >>= 8
    return (~crc) & 0xFFFFFFFF


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
    """hash(sanitized session name) -> palette hex, or None for unnamed (bare-digit) names.
    Hashes the SANITIZED form so the bar (which hashes plain workspace names)
    and the pad/tmux side (which hashes the sanitized session name) agree even
    when a name needs sanitizing, e.g. workspace "wacky sax" -> session
    "wacky-sax"."""
    if not name or name.isdigit():
        return None
    pal = PALETTE_LIGHT if light else PALETTE_DARK
    return pal[posix_cksum(session_name(name).encode()) % len(pal)]


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
    "monitoradded", "monitorremoved",
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
