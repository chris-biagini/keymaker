"""Cockpit: the switchboard deck. Keys are sticky slots over every running
terminal window, one page (12 slots) at a time; knob pages; OLED = identity.
(Task 7 rebuilds the OLED's lower two lines; blank for now.)"""
from adafruit_ticks import ticks_diff, ticks_ms

import km_deck
import km_palette
from km_keys import KeyTracker
from km_text import header_line

from pad.framework import App
from pad.ui import WIDTH_CHARS


class Cockpit(App):
    name = "cockpit"

    def __init__(self):
        self.deck = {"t": "deck", "page": 0, "pages": 1,
                     "ws": [], "slots": [], "map": [0], "bells": []}
        self.flags = {"submap": "", "screencast": False}
        self.palette = dict(km_palette.DEFAULT)
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # Last frame actually written to the strip. `pixels.auto_write` stays True
        # (see framework.py) so every `pixels[i] = ...` drives the whole strip
        # immediately; writing only the pixels that changed keeps a static deck
        # at zero writes per tick instead of a clear-then-repaint strobe. None
        # forces a full first paint.
        self._led_frame = [None] * km_deck.SLOTS_PER_PAGE
        # Last text/signature actually written to each OLED surface, so
        # _draw_text can skip a redundant write instead of dirtying the panel
        # every tick.
        self._map_sig = None
        self._header_text = None
        self._line1_text = None
        self._line2_text = None

    def on_show(self):
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # A ledtest repaints via on_show; a stale cache would leave that debug
        # frame (LEDs) or the last app's stale text (OLED) stuck instead of
        # being redrawn.
        self._led_frame = [None] * km_deck.SLOTS_PER_PAGE
        self._map_sig = None
        self._header_text = None
        self._line1_text = None
        self._line2_text = None
        self._draw_all(ticks_ms())

    def on_msg(self, msg):
        t = msg["t"]
        if t == "deck":
            self.deck = msg
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

    def tick(self, now):
        for n in self.tracker.tick(now):
            self.link.send({"t": "key", "n": n, "act": "hold"})
        self._draw_leds(now)          # every pass: urgent pulse animation
        self._draw_text(now)          # minimap blink needs time too

    # ---- drawing --------------------------------------------------
    def _draw_leds(self, now):
        # Same triangle wave the split deck used; phase is computed inline here,
        # there is no _pulse_phase helper.
        phase = (now % 1000) / 1000
        phase = phase * 2 if phase < 0.5 else (1 - phase) * 2
        frame = [0x000000] * km_deck.SLOTS_PER_PAGE
        if self.link.up:
            for slot in self.deck["slots"]:
                ws_hex = self.deck["ws"][slot["c"]][1]
                frame[slot["i"]] = km_palette.deck_key_color(slot["s"], ws_hex, phase)
        # auto_write is True (framework.py:114-116), so every pixel assignment
        # drives the whole strip immediately. Writing only the pixels that
        # changed keeps a static deck at zero writes per tick and stops the
        # clear-then-repaint pass from strobing the hardware.
        for i, c in enumerate(frame):
            if c != self._led_frame[i]:
                self.pad.pixels[i] = c
        self._led_frame = frame

    def _draw_text(self, now):
        if not self.link.up:
            self.screen.idle_card()
            # idle_card writes header/line1/line2 directly, bypassing the
            # caches below; invalidate them so link recovery forces a full
            # repaint instead of comparing against what idle_card left on screen.
            self._header_text = self._line1_text = self._line2_text = None
            self._map_sig = None
            return
        d = self.deck
        blink = (now // 450) % 2 == 0
        # len(d["map"]), NOT d["pages"]: km_deck caps `map` at MINIMAP_MAX_PAGES (5)
        # because only five boxes fit in the 94px minimap strip alongside the
        # counts column, while `pages` counts every page the KEYS can reach.
        # Passing `pages` would draw boxes off the right edge of the strip.
        #
        # Only recompute when an input actually changed. The blink phase belongs
        # in the signature ONLY when something is ringing: it flips twice a
        # second forever, so including it unconditionally means a redraw twice a
        # second on a completely idle deck -- which is exactly what read as a
        # periodic blink on the panel. With no bells, blink changes nothing that
        # gets drawn, so it must not force the redraw either.
        sig = (blink and bool(d["bells"]), d["page"], tuple(d["map"]),
               tuple(d["bells"]))
        if sig != self._map_sig:
            self._map_sig = sig
            self.screen.set_minimap(len(d["map"]), d["page"], d["map"], d["bells"], blink)
        # Badges carried over from the pre-switchboard header. Mode is
        # shortened to P1/2 (was PAGE 1/2) to leave room for badges inside
        # the 21-column header. Composition (which badges survive when the
        # line is tight, and never truncating the mode) is km_text.header_line's
        # job, not inline arithmetic here -- that arithmetic proved buggy once
        # already (round 3: it ate "P10/12" down to "P1" under badge pressure)
        # while sitting in untestable firmware.
        badges = []
        if self.flags["screencast"]:
            badges.append("REC")
        if self.flags["submap"]:
            badges.append("[" + self.flags["submap"] + "]")
        mode = "P%d/%d" % (d["page"] + 1, d["pages"])
        header = header_line("nexus", badges, mode, WIDTH_CHARS)
        # set_header touches every glyph in the inverted bar; skip the write
        # when the text hasn't changed instead of re-setting it every tick.
        if header != self._header_text:
            self._header_text = header
            self.screen.set_header(header)
        # Task 7 rebuilds the OLED's lower two lines; blank for now rather
        # than stale.
        line1 = ""
        line2 = ""
        if line1 != self._line1_text:
            self._line1_text = line1
            self.screen.line1.text = line1
        if line2 != self._line2_text:
            self._line2_text = line2
            self.screen.line2.text = line2
        # Minimap counts column (spec 8.2): total occupied slots on the pages
        # the minimap can draw, plus how many unacked bells are on a page that
        # isn't the one currently on the keys -- the number the blinking cell
        # alone can't convey once it's off-page. `map` is a per-page bitmask
        # (km_deck.Deck.message), so the total is a popcount, not a sum --
        # bin() is available on CircuitPython.
        total = sum(bin(m).count("1") for m in d["map"])
        off_page = sum(1 for g in d["bells"] if g // km_deck.SLOTS_PER_PAGE != d["page"])
        self.screen.footer.text = ("%d+%d!" % (total, off_page))[:5] if off_page else str(total)[:5]

    def _draw_all(self, now):
        self._draw_leds(now)
        self._draw_text(now)
