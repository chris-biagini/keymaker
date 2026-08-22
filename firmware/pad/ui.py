"""OLED layout: inverted header / focused window / four legend rows over a
state-gutter bitmap. Spec section 5."""
import displayio
import terminalio
from adafruit_display_text import label

import km_deck

WIDTH_CHARS = 21   # 128px / 6px font
MAP_Y = 23         # bitmap origin; rows 23-61
MAP_H = 39


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        self.header = label.Label(terminalio.FONT, text="", x=0, y=6,
                                  color=0x000000, background_color=0xFFFFFF)
        self.focus = label.Label(terminalio.FONT, text="", x=0, y=18)
        # Legend text sits at x=5 so the 4px state gutter at x=1 stays clear.
        # Rows are 10px apart, matching km_deck.GUTTER_PITCH_Y.
        self.rows = [label.Label(terminalio.FONT, text="", x=5, y=29 + i * 10)
                     for i in range(km_deck.LEGEND_ROWS)]
        self.group.append(self.header)
        self.group.append(self.focus)
        for r in self.rows:
            self.group.append(r)
        self.map_bmp = displayio.Bitmap(128, MAP_H, 2)
        pal = displayio.Palette(2)
        pal[0] = 0x000000
        pal[1] = 0xFFFFFF
        self.group.append(displayio.TileGrid(self.map_bmp, pixel_shader=pal,
                                             x=0, y=MAP_Y))
        # Lit pixels currently on the bitmap, so set_gutters can paint the
        # difference rather than clear and repaint. See km_deck.gutter_pixels.
        self._lit = set()
        display.root_group = self.group

    def set_header(self, text):
        # space-pad so the inverted bar always spans the full width
        t = text[:WIDTH_CHARS]
        self.header.text = t + " " * (WIDTH_CHARS - len(t))

    def set_focus(self, text):
        self.focus.text = text[:WIDTH_CHARS]

    def set_legend(self, rows):
        for i, r in enumerate(self.rows):
            r.text = rows[i] if i < len(rows) else ""

    def set_gutters(self, lit):
        """Paint the DIFFERENCE against what is already on the bitmap.

        Never a fill(0) first: displayio refreshes the panel on its own schedule
        (auto_refresh is on), so a clear-then-repaint hands it a blank frame to
        scan out, which is what reads as a flicker. An unchanged frame writes
        nothing at all.
        """
        for x, y in self._lit - lit:
            self.map_bmp[x, y] = 0
        for x, y in lit - self._lit:
            self.map_bmp[x, y] = 1
        self._lit = lit

    def idle_card(self):
        self.set_header("keymaker")
        self.focus.text = "no link"
        self.set_legend([""] * km_deck.LEGEND_ROWS)
        # Clear the gutters too: with the link down the deck they describe is
        # stale, and leaving them lit beside "no link" claims windows we can no
        # longer see.
        self.set_gutters(set())
