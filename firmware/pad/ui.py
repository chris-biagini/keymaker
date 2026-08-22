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
        display.root_group = self.group

    def set_header(self, text):
        # space-pad so the inverted bar always spans the full width
        t = text[:WIDTH_CHARS]
        self.header.text = t + " " * (WIDTH_CHARS - len(t))

    def set_minimap(self, pages, page, counts, bells, blink):
        """One mini 3x4 deck per page. Spec section 8.2."""
        import km_deck
        self.map_bmp.fill(0)
        boxes = km_deck.minimap_boxes(pages, y=0)
        for p, box in enumerate(boxes):
            # `page` can exceed the drawable range: the keys reach every page, the
            # minimap only shows the first MINIMAP_MAX_PAGES. When the user is past
            # that, nothing is outlined and the header's "PAGE 7/9" carries the
            # position instead. Deliberate -- an outline on the wrong box would lie.
            if p == page:                       # outline the page on the keys
                x, y, w, h = box
                for dx in range(w):
                    self.map_bmp[x + dx, y] = 1
                    self.map_bmp[x + dx, y + h - 1] = 1
                for dy in range(h):
                    self.map_bmp[x, y + dy] = 1
                    self.map_bmp[x + w - 1, y + dy] = 1
            for i in range(min(counts[p] if p < len(counts) else 0, 12)):
                cx, cy = km_deck.minimap_cell(p * 12 + i, box)
                for dx in range(2):
                    for dy in range(2):
                        self.map_bmp[cx + dx, cy + dy] = 1
        # Bells draw LAST and larger, so an alert is never overdrawn by a plain
        # cell and reads as different even when caught mid-blink.
        if blink:
            for gslot in bells:
                p = gslot // 12
                if p >= len(boxes):
                    continue
                cx, cy = km_deck.minimap_cell(gslot, boxes[p])
                for dx in range(3):
                    for dy in range(3):
                        self.map_bmp[cx + dx, cy + dy] = 1

    def idle_card(self):
        self.set_header("keymaker")
        self.line1.text = "no link"
        self.line2.text = ""
        self.footer.text = ""
