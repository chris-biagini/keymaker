"""OLED layout: inverted header / two body lines / footer + idle card."""
import displayio
import terminalio
from adafruit_display_text import label

WIDTH_CHARS = 21   # 128px / 6px font


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        self.header = label.Label(terminalio.FONT, text="", x=0, y=6,
                                  color=0x000000, background_color=0xFFFFFF)
        self.line1 = label.Label(terminalio.FONT, text="", x=0, y=26)
        self.line2 = label.Label(terminalio.FONT, text="", x=0, y=42)
        self.footer = label.Label(terminalio.FONT, text="", x=0, y=58)
        for l in (self.header, self.line1, self.line2, self.footer):
            self.group.append(l)
        display.root_group = self.group

    def set_header(self, text):
        # space-pad so the inverted bar always spans the full width
        t = text[:WIDTH_CHARS]
        self.header.text = t + " " * (WIDTH_CHARS - len(t))

    def idle_card(self):
        self.set_header("keymaker")
        self.line1.text = "no link"
        self.line2.text = ""
        self.footer.text = ""
