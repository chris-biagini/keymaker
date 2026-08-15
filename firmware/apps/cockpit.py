"""Cockpit: 12 keys = Hyprland workspaces 1-12, knob = volume, OLED = focus."""
from adafruit_ticks import ticks_diff, ticks_ms

import km_palette
from km_keys import KeyTracker
from km_text import marquee

from pad.framework import App
from pad.ui import WIDTH_CHARS


class Cockpit(App):
    name = "cockpit"

    def __init__(self):
        self.ws = {"active": 1, "occupied": [], "urgent": [], "colors": {}}
        self.win = {"cls": "", "title": ""}
        self.flags = {"submap": "", "screencast": False, "muted": False}
        self.palette = dict(km_palette.DEFAULT)
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)

    def on_show(self):
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
        for n in range(12):
            self.pad.pixels[n] = km_palette.ws_key_color(
                self._key_state(n), colors.get(str(n + 1)), self.palette, phase)

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
        self.screen.header.text = ("ws " + str(self.ws["active"]) + badges)[:WIDTH_CHARS]
        self.screen.title.text = marquee(self.win["title"], WIDTH_CHARS, now)
        self.screen.footer.text = self.win["cls"][:WIDTH_CHARS]

    def _draw_all(self, now):
        self._draw_leds(now)
        self._draw_text(now)
