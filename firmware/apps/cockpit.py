"""Cockpit: keys 0-5 = workspaces 1-6, keys 6-11 = tmux windows of the active
workspace's footguard session; knob = volume; OLED = identity + attention ledger."""
from adafruit_ticks import ticks_diff, ticks_ms

import km_palette
from km_keys import KeyTracker
from km_text import marquee

from pad.framework import App
from pad.ui import WIDTH_CHARS


class Cockpit(App):
    name = "cockpit"

    def __init__(self):
        self.ws = {"active": 1, "occupied": [], "urgent": [], "colors": {},
                   "names": {}}
        self.ctx = {"t": "ctx", "mode": "none", "session": None, "items": []}
        self._ctx_by_i = {}
        self.ledger = {"t": "ledger", "claudes": [], "bells": []}
        self.win = {"cls": "", "title": ""}
        self.flags = {"submap": "", "screencast": False, "muted": False}
        self.palette = dict(km_palette.DEFAULT)
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)

    def on_show(self):
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        self._draw_all(ticks_ms())

    def on_msg(self, msg):
        t = msg["t"]
        if t == "ws":
            self.ws = msg
        elif t == "win":
            self.win = msg
        elif t == "flags":
            self.flags = msg
        elif t == "palette":
            self.palette = msg
        elif t == "ctx":
            self.ctx = msg
            self._ctx_by_i = {}
            for it in msg.get("items", []):
                self._ctx_by_i[it["i"]] = it
        elif t == "ledger":
            self.ledger = msg
        self._draw_all(ticks_ms())

    def on_key_event(self, n, pressed, now):
        if pressed:
            self.tracker.press(n, now)
        elif self.tracker.release(n, now) == "tap":
            self.link.send({"t": "key", "n": n, "act": "tap"})

    def on_dial(self, delta):
        self.link.send({"t": "dial", "d": delta})

    def on_click(self):
        self.link.send({"t": "click"})

    def tick(self, now):
        for n in self.tracker.tick(now):
            self.link.send({"t": "key", "n": n, "act": "hold"})
        self._draw_leds(now)          # every pass: urgent pulse animation
        self._draw_text(now)          # marquee needs time too

    # ---- drawing --------------------------------------------------
    def _key_state(self, n):
        ws = n + 1
        if not self.link.up:
            return "empty"
        if ws == self.ws["active"]:
            return "active"
        if ws in self.ws["urgent"]:
            return "urgent"
        if ws in self.ws["occupied"]:
            return "occupied"
        return "empty"

    def _draw_leds(self, now):
        phase = (now % 1000) / 1000
        phase = phase * 2 if phase < 0.5 else (1 - phase) * 2   # triangle wave
        colors = self.ws.get("colors", {})
        for n in range(6):
            self.pad.pixels[n] = km_palette.ws_key_color(
                self._key_state(n), colors.get(str(n + 1)), self.palette, phase)
        for n in range(6, 12):
            item = self._ctx_by_i.get(n - 5) if self.link.up else None
            self.pad.pixels[n] = km_palette.ctx_key_color(item, self.palette, phase)

    def _draw_text(self, now):
        if not self.link.up:
            self.screen.idle_card()
            return
        badges = ""
        if self.flags["screencast"]:
            badges += " REC"
        if self.flags["muted"]:
            badges += " MUTE"
        if self.flags["submap"]:
            badges += " [" + self.flags["submap"] + "]"
        active = self.ws["active"]
        name = self.ws.get("names", {}).get(str(active))
        ident = (str(active) + " - " + name) if name else ("ws " + str(active))
        self.screen.set_header(ident + badges)
        # Attention ledger: what is waiting on you, machine-wide. Blank lines
        # mean all clear -- a glanceable state in itself, and it reads across a
        # locked screen because the pad is a display hyprlock cannot cover.
        claudes = self.ledger.get("claudes", [])
        bells = self.ledger.get("bells", [])
        waiting = [c for c in claudes if not c.get("busy")]
        busy = len(claudes) - len(waiting)
        if waiting:
            text = " | ".join(c.get("s", "?") + ": " + c.get("title", "") for c in waiting)
            self.screen.line1.text = marquee("* " + text, WIDTH_CHARS, now)
        else:
            self.screen.line1.text = ""
        if bells:
            text = " ".join(b.get("s", "?") + ":" + str(b.get("i", "?")) for b in bells)
            self.screen.line2.text = marquee("! " + text, WIDTH_CHARS, now)
        else:
            self.screen.line2.text = ""
        self.screen.footer.text = (str(busy) + " working") if busy else ""

    def _draw_all(self, now):
        self._draw_leds(now)
        self._draw_text(now)
