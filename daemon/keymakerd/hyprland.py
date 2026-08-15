"""Hyprland IPC: instance discovery, one-shot requests, event stream state."""
import asyncio
import re
from pathlib import Path

_WS_COLOR_RE = re.compile(r"foreground=['\"]#?([0-9a-fA-F]{6})")


def ws_color(name):
    """Workspace-identity color embedded in a workspace name, or None."""
    m = _WS_COLOR_RE.search(name or "")
    return m.group(1).lower() if m else None


REFRESH_EVENTS = {
    "workspace", "workspacev2", "focusedmon", "openwindow", "closewindow",
    "movewindow", "activewindow", "activewindowv2", "urgent", "fullscreen",
    "monitoradded", "monitorremoved",
}


def find_instance_dir(runtime):
    best = None
    for d in Path(runtime).glob("hypr/*"):
        if (d / ".socket.sock").exists():
            if best is None or d.stat().st_mtime > best.stat().st_mtime:
                best = d
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
        self.cls = ""
        self.title = ""
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
        if name == "urgent":
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
        colors = {}
        for w in workspaces:
            c = ws_color(w.get("name"))
            if c is not None:
                colors[str(w["id"])] = c
        if (active, occupied, urgent, colors) != (self.active, self.occupied, self.urgent_ws, self.colors):
            self.active, self.occupied, self.urgent_ws, self.colors = active, occupied, urgent, colors
            msgs.append(self._ws_msg())
        cls = (active_win or {}).get("class", "")
        title = (active_win or {}).get("title", "")[:60]
        if (cls, title) != (self.cls, self.title):
            self.cls, self.title = cls, title
            msgs.append(self._win_msg())
        return msgs

    def snapshot(self):
        return [self._ws_msg(), self._win_msg()]

    def _ws_msg(self):
        return {"t": "ws", "active": self.active, "occupied": self.occupied,
                "urgent": self.urgent_ws, "colors": self.colors}

    def _win_msg(self):
        return {"t": "win", "cls": self.cls, "title": self.title}
