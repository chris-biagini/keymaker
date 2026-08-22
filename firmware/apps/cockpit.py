"""Cockpit: the switchboard deck. Keys are sticky slots over every running
terminal window, one page (12 slots) at a time; knob pages; OLED = identity.
The lower two lines are a four-row, twelve-cell legend plus a state-gutter
bitmap; see firmware/pad/ui.py and shared/km_deck.py."""
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
        self.deck = {"t": "deck", "page": 0, "pages": 1, "total": 0, "focus": "",
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
        # Last text actually written to each OLED surface, so _draw_text can
        # skip a redundant write instead of dirtying the panel every tick.
        self._header_text = None
        self._focus_text = None
        self._legend = None
        # A re-key countdown owns the focused row while a hold is in progress,
        # and an in-progress hold's encoder-down state. Both are set only here,
        # never in on_show -- a hold in progress must not survive a repaint.
        self._countdown = None
        self._enc_down = None

    def on_show(self):
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)
        # A ledtest repaints via on_show; a stale cache would leave that debug
        # frame (LEDs) or the last app's stale text (OLED) stuck instead of
        # being redrawn.
        self._led_frame = [None] * km_deck.SLOTS_PER_PAGE
        self._header_text = None
        self._focus_text = None
        self._legend = None
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

    def on_enc(self, pressed, now):
        if pressed:
            self._enc_down = now
            return
        # Released. Fire only if the hold ran the full countdown; anything
        # shorter is an abort, and an abort sends nothing at all.
        if self._enc_down is not None:
            held = ticks_diff(now, self._enc_down)
            if held >= km_deck.REKEY_FIRE_MS:
                self.link.send({"t": "rekey"})
        self._enc_down = None
        self._countdown = None

    def _tick_countdown(self, now):
        if self._enc_down is None:
            self._countdown = None
            return
        self._countdown = km_deck.countdown_text(ticks_diff(now, self._enc_down))

    def tick(self, now):
        self._tick_countdown(now)
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
            # idle_card writes header/focus/legend/gutters directly, bypassing
            # the caches below; invalidate them so link recovery forces a full
            # repaint instead of comparing against what idle_card left on screen.
            self._header_text = self._focus_text = None
            self._legend = None
            return
        d = self.deck
        blink = (now // 450) % 2 == 0

        badges = []
        if self.flags["screencast"]:
            badges.append("REC")
        if self.flags["submap"]:
            badges.append("[" + self.flags["submap"] + "]")
        mode = "P%d/%d %dw" % (d["page"] + 1, d["pages"], d["total"])
        header = header_line("nexus", badges, mode, WIDTH_CHARS)
        if header != self._header_text:
            self._header_text = header
            self.screen.set_header(header)

        # A re-key countdown owns the focused row while a hold is in progress.
        focus = self._countdown if self._countdown is not None else d["focus"]
        focus = marquee(focus, WIDTH_CHARS, now)
        if focus != self._focus_text:
            self._focus_text = focus
            self.screen.set_focus(focus)

        labels = [" " * km_deck.CELL_CHARS] * km_deck.SLOTS_PER_PAGE
        states = ["empty"] * km_deck.SLOTS_PER_PAGE
        for slot in d["slots"]:
            ws = d["ws"][slot["c"]][0]
            labels[slot["i"]] = km_deck.cell_label(ws, slot["n"])
            states[slot["i"]] = slot["s"]
        legend = [km_deck.legend_row(labels, r) for r in range(km_deck.LEGEND_ROWS)]
        # Labels are rewritten only when the deck actually changes; the blink
        # below never touches them. See docs/pad-timing.md section 5.
        if legend != self._legend:
            self._legend = legend
            self.screen.set_legend(legend)
        self.screen.set_gutters(km_deck.gutter_pixels(states, blink))

    def _draw_all(self, now):
        self._draw_leds(now)
        self._draw_text(now)
