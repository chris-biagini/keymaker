"""OLED layout: three-line screen (header / title / footer) + idle card."""
import displayio
import terminalio
from adafruit_display_text import label

WIDTH_CHARS = 21   # 128px / 6px font


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        self.header = label.Label(terminalio.FONT, text="", x=0, y=6)
        self.title = label.Label(terminalio.FONT, text="", x=0, y=30)
        self.footer = label.Label(terminalio.FONT, text="", x=0, y=58)
        for l in (self.header, self.title, self.footer):
            self.group.append(l)
        display.root_group = self.group

    def idle_card(self):
        self.header.text = "keymaker"
        self.title.text = "no link"
        self.footer.text = ""
