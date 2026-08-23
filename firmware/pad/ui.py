"""OLED layout: inverted header + focused-window line.

Deliberately minimal: the split deck carries its information on the keys, and
the OLED's real design is a separate piece of work. When extending this, mind
docs/pad-timing.md -- Label.text has no equality short-circuit, so callers must
cache what they last wrote and skip redundant writes.
"""
import displayio
import terminalio
from adafruit_display_text import label

WIDTH_CHARS = 21   # 128px / 6px font


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        self.header = label.Label(terminalio.FONT, text="", x=0, y=6,
                                  color=0x000000, background_color=0xFFFFFF)
        self.focus = label.Label(terminalio.FONT, text="", x=0, y=18)
        self.group.append(self.header)
        self.group.append(self.focus)
        display.root_group = self.group

    def set_header(self, text):
        # space-pad so the inverted bar always spans the full width
        t = text[:WIDTH_CHARS]
        self.header.text = t + " " * (WIDTH_CHARS - len(t))

    def set_focus(self, text):
        self.focus.text = text[:WIDTH_CHARS]

    def idle_card(self):
        self.set_header("keymaker")
        self.focus.text = "no link"
