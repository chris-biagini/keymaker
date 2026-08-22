"""Cockpit: the switchboard deck. Keys are sticky slots over every running
terminal window, one page (12 slots) at a time; knob pages or adjusts volume
depending on daemon-side mode; OLED = identity + attention ledger."""
from adafruit_ticks import ticks_diff, ticks_ms

import km_deck
import km_palette
from km_keys import KeyTracker
from km_text import header_line, marquee

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
        # Last frame actually written to the strip. `pixels.auto_write` stays True
        # (see framework.py) so every `pixels[i] = ...` drives the whole strip
        # immediately; writing only the pixels that changed keeps a static deck
        # at zero writes per tick instead of a clear-then-repaint strobe. None
        # forces a full first paint.
        self._led_frame = [None] * km_deck.SLOTS_PER_PAGE

    def on_show(self):
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # A ledtest repaints via on_show; a stale cache would leave that debug
        # frame stuck instead of being redrawn.
        self._led_frame = [None] * km_deck.SLOTS_PER_PAGE
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
            return
        d = self.deck
        blink = (now // 450) % 2 == 0
        # len(d["map"]), NOT d["pages"]: km_deck caps `map` at MINIMAP_MAX_PAGES (5)
        # because only five boxes fit in the 94px minimap strip alongside the
        # counts column, while `pages` counts every page the KEYS can reach.
        # Passing `pages` would draw boxes off the right edge of the strip.
        self.screen.set_minimap(len(d["map"]), d["page"], d["map"], d["bells"], blink)
        # Badges carried over from the pre-switchboard header; a separate
        # ruling in this plan removed the pad's mute *gesture*, so dropping
        # its indicator too would take away the control and its display in
        # the same release. Mode is shortened to P1/2 (was PAGE 1/2) to leave
        # room for badges inside the 21-column header. Composition (which
        # badges survive when the line is tight, and never truncating the
        # mode) is km_text.header_line's job, not inline arithmetic here --
        # that arithmetic proved buggy once already (round 3: it ate "P10/12"
        # down to "P1" under badge pressure) while sitting in untestable
        # firmware.
        badges = []
        if self.flags["screencast"]:
            badges.append("REC")
        if self.flags["muted"]:
            badges.append("MUTE")
        if self.flags["submap"]:
            badges.append("[" + self.flags["submap"] + "]")
        mode = ("P%d/%d" % (d["page"] + 1, d["pages"])) if d["knob"] == "page" else "VOL"
        self.screen.set_header(header_line("nexus", badges, mode, WIDTH_CHARS))
        # Attention ledger: what is waiting on you, machine-wide. Blank lines
        # mean all clear -- a glanceable state in itself, and it reads across a
        # locked screen because the pad is a display hyprlock cannot cover.
        claudes = self.ledger.get("claudes", [])
        bells = self.ledger.get("bells", [])
        waiting = [c for c in claudes if not c.get("busy")]
        busy = len(claudes) - len(waiting)
        # line1 does double duty: waiting Claudes and the busy count are
        # mutually exclusive in practice -- a fleet with something waiting is
        # a fleet you should look at before counting what is merely running --
        # so busy only shows on the row when there is nothing waiting to show
        # instead. Keeps the "N working" indicator without a screen row of
        # its own (footer is now the minimap's counts column).
        if waiting:
            text = " | ".join(c.get("s", "?") + ": " + c.get("title", "") for c in waiting)
            self.screen.line1.text = marquee("* " + text, WIDTH_CHARS, now)
        elif busy:
            self.screen.line1.text = "%d working" % busy
        else:
            self.screen.line1.text = ""
        if bells:
            text = " ".join(b.get("s", "?") + ":" + str(b.get("i", "?")) for b in bells)
            self.screen.line2.text = marquee("! " + text, WIDTH_CHARS, now)
        else:
            self.screen.line2.text = ""
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
