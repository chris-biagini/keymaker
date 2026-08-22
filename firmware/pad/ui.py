"""OLED layout: inverted header / two body lines / minimap strip with a
right-hand counts column / idle card."""
import displayio
import terminalio
from adafruit_display_text import label

WIDTH_CHARS = 21   # 128px / 6px font


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        self.header = label.Label(terminalio.FONT, text="", x=0, y=6,
                                  color=0x000000, background_color=0xFFFFFF)
        self.line1 = label.Label(terminalio.FONT, text="", x=0, y=20)
        self.line2 = label.Label(terminalio.FONT, text="", x=0, y=32)
        # Repurposed as the right-hand minimap counts column (see set_minimap
        # callers) now that the minimap owns its own strip below line2.
        self.footer = label.Label(terminalio.FONT, text="", x=96, y=52)
        for l in (self.header, self.line1, self.line2, self.footer):
            self.group.append(l)
        # 94x22 mono, clear of the footer's counts column at x=96. A Bitmap is
        # the cheapest surface for per-pixel work; the existing labels stay as
        # labels. TileGrid at y=40 puts its rows at 40-61: below line2 (rows
        # ~26-34 per the y-6..y+2 glyph band terminalio labels occupy) and
        # inside the 64px panel.
        self.map_bmp = displayio.Bitmap(94, 22, 2)
        pal = displayio.Palette(2)
        pal[0] = 0x000000
        pal[1] = 0xFFFFFF
        self.map_tile = displayio.TileGrid(self.map_bmp, pixel_shader=pal, x=0, y=40)
        self.group.append(self.map_tile)
        # Lit pixels currently on the bitmap, so set_minimap can paint the
        # difference rather than clear and repaint. See km_deck.minimap_pixels.
        self._map_lit = set()
        display.root_group = self.group

    def set_header(self, text):
        # space-pad so the inverted bar always spans the full width
        t = text[:WIDTH_CHARS]
        self.header.text = t + " " * (WIDTH_CHARS - len(t))

    def set_minimap(self, pages, page, masks, bells, blink):
        """One mini 3x4 deck per page. Spec section 8.2.

        Paints the DIFFERENCE against what is already on the bitmap -- never a
        fill(0) first. displayio refreshes the panel on its own schedule
        (auto_refresh is on), so a clear-then-repaint hands it a blank frame to
        scan out, which is what reads as a flicker. Only pixels that actually
        change are written, so a redraw of an unchanged minimap touches nothing.

        The geometry itself lives in km_deck.minimap_pixels, where pytest can
        reach it; this method is only the diff and the writes.
        """
        import km_deck
        want = km_deck.minimap_pixels(pages, page, masks, bells, blink, y=0)
        for x, y in self._map_lit - want:
            self.map_bmp[x, y] = 0
        for x, y in want - self._map_lit:
            self.map_bmp[x, y] = 1
        self._map_lit = want

    def idle_card(self):
        self.set_header("keymaker")
        self.line1.text = "no link"
        self.line2.text = ""
        self.footer.text = ""
        # Clear the minimap too: with the link down the deck it describes is
        # stale, and leaving it lit next to "no link" claims windows we can no
        # longer see. Goes through the same diff bookkeeping so the next
        # set_minimap repaints from a correct notion of what is on the bitmap.
        self.set_minimap(0, 0, (), (), False)
