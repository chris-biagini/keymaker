"""Cockpit: the switchboard deck. Keys are sticky slots over every running
terminal window, one page (12 slots) at a time; knob pages or adjusts volume
depending on daemon-side mode; OLED = identity + attention ledger."""
from adafruit_ticks import ticks_diff, ticks_ms

import km_palette
from km_keys import KeyTracker
from km_text import marquee

from pad.framework import App
from pad.ui import WIDTH_CHARS


class Cockpit(App):
    name = "cockpit"

    def __init__(self):
        self.deck = {"t": "deck", "page": 0, "pages": 1, "knob": "vol",
                     "ws": [], "slots": [], "map": [0], "bells": []}
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
        if t == "deck":
            self.deck = msg
        elif t == "win":
            self.win = msg
        elif t == "flags":
            self.flags = msg
        elif t == "palette":
            self.palette = msg
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
    def _draw_leds(self, now):
        # Same triangle wave the split deck used; phase is computed inline here,
        # there is no _pulse_phase helper.
        phase = (now % 1000) / 1000
        phase = phase * 2 if phase < 0.5 else (1 - phase) * 2
        for i in range(12):
            self.pad.pixels[i] = 0x000000
        if not self.link.up:
            return
        for slot in self.deck["slots"]:
            ws_hex = self.deck["ws"][slot["c"]][1]
            self.pad.pixels[slot["i"]] = km_palette.deck_key_color(
                slot["s"], ws_hex, phase)

    def _draw_text(self, now):
        if not self.link.up:
            self.screen.idle_card()
            return
        d = self.deck
        blink = (now // 450) % 2 == 0
        # len(d["map"]), NOT d["pages"]: km_deck caps `map` at MINIMAP_MAX_PAGES (5)
        # because only five boxes fit in 128px alongside the counts text, while
        # `pages` counts every page the KEYS can reach. Passing `pages` would draw
        # boxes off the right edge of the screen.
        self.screen.set_minimap(len(d["map"]), d["page"], d["map"], d["bells"], blink)
        mode = ("PAGE %d/%d" % (d["page"] + 1, d["pages"])) if d["knob"] == "page" else "VOL"
        self.screen.set_header("nexus" + " " * (WIDTH_CHARS - 5 - len(mode)) + mode)
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
