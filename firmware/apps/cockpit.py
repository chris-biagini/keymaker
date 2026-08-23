"""Cockpit: the split deck. Keys 0-5 = workspaces 1-6 in their colorhash
colors; keys 6-11 = windows on the active workspace (tmux windows of its ws-*
session, then sessionless terminals); OLED = identity header + focused window."""
from adafruit_ticks import ticks_diff, ticks_ms

import km_palette
from km_keys import KeyTracker
from km_text import header_line, marquee

from pad.framework import App
from pad.ui import WIDTH_CHARS

KEYS = 12


class Cockpit(App):
    name = "cockpit"

    def __init__(self):
        self.ws = {"active": 1, "occupied": [], "urgent": [], "colors": {},
                   "names": {}}
        self.ctx = {"t": "ctx", "items": []}
        self.win = {"cls": "", "title": ""}
        self.flags = {"submap": "", "screencast": False}
        self.palette = dict(km_palette.DEFAULT)
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # Last frame actually written to the strip. `pixels.auto_write` stays True
        # (see framework.py) so every `pixels[i] = ...` drives the whole strip
        # immediately; writing only the pixels that changed keeps a static deck
        # at zero writes per tick instead of a clear-then-repaint strobe. None
        # forces a full first paint.
        self._led_frame = [None] * KEYS
        # Last text actually written to each OLED surface, so _draw_text can
        # skip a redundant write instead of dirtying the panel every tick.
        self._header_text = None
        self._focus_text = None
        # Latches "idle_card has already been painted this outage" so the
        # no-link branch writes it once per outage rather than every tick --
        # Label.text has no equality short-circuit (docs/pad-timing.md).
        self._idle = False

    def on_show(self):
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # A ledtest repaints via on_show; a stale cache would leave that debug
        # frame (LEDs) or stale text (OLED) stuck instead of being redrawn.
        self._led_frame = [None] * KEYS
        self._header_text = None
        self._focus_text = None
        self._draw_all(ticks_ms())

    def on_msg(self, msg):
        t = msg["t"]
        if t == "ws":
            self.ws = msg
        elif t == "ctx":
            self.ctx = msg
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

    def tick(self, now):
        for n in self.tracker.tick(now):
            self.link.send({"t": "key", "n": n, "act": "hold"})
        self._draw_leds(now)          # every pass: urgent pulse animation
        self._draw_text(now)          # marquee needs time too

    # ---- drawing --------------------------------------------------
    def _ws_state(self, n):
        ws = n + 1
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
        frame = [0x000000] * KEYS
        if self.link.up:
            colors = self.ws.get("colors", {})
            for n in range(6):
                frame[n] = km_palette.ws_key_color(
                    self._ws_state(n), colors.get(str(n + 1)), self.palette, phase)
            items = self.ctx.get("items", [])
            for n in range(6, KEYS):
                item = items[n - 6] if n - 6 < len(items) else None
                frame[n] = km_palette.ctx_key_color(item, self.palette, phase)
        # auto_write is True (framework.py), so every pixel assignment drives
        # the whole strip immediately. Writing only the pixels that changed
        # keeps a static deck at zero writes per tick and stops the
        # clear-then-repaint pass from strobing the hardware.
        for i, c in enumerate(frame):
            if c != self._led_frame[i]:
                self.pad.pixels[i] = c
        self._led_frame = frame

    def _draw_text(self, now):
        if not self.link.up:
            if not self._idle:
                self.screen.idle_card()
                # idle_card writes header/focus directly, bypassing the caches;
                # invalidate them so link recovery forces a full repaint.
                self._header_text = self._focus_text = None
                self._idle = True
            return
        self._idle = False
        badges = []
        if self.flags["screencast"]:
            badges.append("REC")
        if self.flags["submap"]:
            badges.append("[" + self.flags["submap"] + "]")
        active = self.ws["active"]
        name = self.ws.get("names", {}).get(str(active))
        ident = (str(active) + " " + name) if name else ("ws " + str(active))
        header = header_line(ident, badges, "", WIDTH_CHARS)
        if header != self._header_text:
            self._header_text = header
            self.screen.set_header(header)
        focus = marquee(self.win.get("title", ""), WIDTH_CHARS, now)
        if focus != self._focus_text:
            self._focus_text = focus
            self.screen.set_focus(focus)

    def _draw_all(self, now):
        self._draw_leds(now)
        self._draw_text(now)
