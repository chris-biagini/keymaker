"""OLED weather: layered scene graph -- rain / bell wall / marquee / badges.

All decisions live in km_weather (host-tested); this file only translates
its outputs into displayio mutations. Discipline per docs/pad-timing.md
section 5: every write path diffs against what was last written; hidden or
static layers cost zero work; no live bitmap is ever cleared wholesale.

Layer order, bottom to top (see Screen.__init__):

  0  base     rain TileGrid + the inverted "no link" corner tag
  1  wall     the bell wall's numerals (shown only in "ringing")
  2  marquee  the workspace-switch wipe numeral
  3  badges   REC + [submap], topmost and always composited last

Every layer's palette makes color 0 transparent, so the layers composite
rather than occlude: the marquee slides over whatever base state is live,
and the badges sit on top of everything.

This module is not host-testable (displayio is firmware-only). It is
therefore written to be mechanical and obvious; anything non-local is
commented rather than left for a reader to re-derive.
"""
import random

import displayio
import terminalio
from adafruit_display_text import label
from adafruit_ticks import ticks_add, ticks_diff, ticks_ms

import km_weather
from assets.digits import DIGITS

FRAME_MS = 100               # ~10 fps animation clock
RAIN_DIV = 2                 # rain advances every 2nd frame (~5 fps)
RAIN_GLYPHS = "01<>=+*:#$KMTXZ7"          # 16 glyphs, matrix-flavored

_KIND_BANK = {"head": 0, "dim": 1}        # tile-sheet banks; "off" = blank

# Right-anchored badge geometry, in terminalio's 6px cells.
_BADGE_CELL = 6
_REC_TEXT = " REC "
_SUBMAP_RIGHT = km_weather.SCREEN_W - len(_REC_TEXT) * _BADGE_CELL - 4
# " [name] " is len(name)+4 cells wide and must not run off the left edge.
_SUBMAP_MAX = _SUBMAP_RIGHT // _BADGE_CELL - 4


def _mono_palette():
    pal = displayio.Palette(2)
    pal[0] = 0x000000
    pal[1] = 0xFFFFFF
    pal.make_transparent(0)   # layers composite over each other
    return pal


def _set_hidden(obj, value):
    """Assign .hidden only on change.

    displayio's hidden setter is not documented to short-circuit, and a
    redundant assignment that marks the group dirty costs a panel refresh
    (docs/pad-timing.md section 5). Reading the attribute is free.
    """
    if obj.hidden != value:
        obj.hidden = value


def _digit_bitmap(rows, scale):
    """Build a 1-bit Bitmap from 14-wide row masks, nearest-neighbor scaled."""
    w, h = km_weather.SMALL_W * scale, km_weather.SMALL_H * scale
    bmp = displayio.Bitmap(w, h, 2)
    for r, mask in enumerate(rows):
        for c in range(km_weather.SMALL_W):
            if mask >> c & 1:
                for dr in range(scale):
                    for dc in range(scale):
                        bmp[c * scale + dc, r * scale + dr] = 1
    return bmp


def _rain_sheet(font):
    """Tile sheet from terminalio glyphs: bank 0 bright, bank 1 dimmed by a
    checkerboard mask (the 1-bit stand-in for 50% gray). Built once at boot.

    Tile 0 is a deliberate blank so the "off" kind is an ordinary tile write
    rather than a special case -- nothing ever clears a live bitmap here.
    """
    box = font.get_bounding_box()
    gw, gh = box[0], box[1]
    n = len(RAIN_GLYPHS)
    sheet = displayio.Bitmap(gw * (1 + 2 * n), gh, 2)
    for i, ch in enumerate(RAIN_GLYPHS):
        glyph = font.get_glyph(ord(ch))
        if glyph is None:
            continue          # missing glyph degrades to a blank tile
        # glyph.bitmap is the font's shared sheet; locate this glyph's tile
        # from tile_index against the sheet's real geometry -- the layout
        # (strip vs grid) is an implementation detail we must not assume.
        src = glyph.bitmap
        per_row = src.width // gw
        sx = (glyph.tile_index % per_row) * gw
        sy = (glyph.tile_index // per_row) * gh
        for y in range(gh):
            for x in range(gw):
                if src[sx + x, sy + y]:
                    sheet[(1 + i) * gw + x, y] = 1              # bright bank
                    # gw is even, so this checkerboard's phase is identical
                    # in every tile and the dither stays coherent on screen.
                    if (x + y) % 2 == 0:
                        sheet[(1 + n + i) * gw + x, y] = 1      # dim bank
    return sheet, gw, gh, n


class Screen:
    def __init__(self, display):
        self._display = display
        self.group = displayio.Group()
        pal = _mono_palette()
        self._pal = pal

        # digit bitmaps, built once at boot: DIGITS index -> Bitmap
        self._small = [_digit_bitmap(rows, 1) for rows in DIGITS]
        self._big = [_digit_bitmap(rows, 2) for rows in DIGITS]

        # --- layer 0: base (rain + the no-link tag) --------------------
        sheet, gw, gh, n = _rain_sheet(terminalio.FONT)
        self._rain_n = n
        cols = km_weather.SCREEN_W // gw
        rows = km_weather.SCREEN_H // gh
        self._rain_grid = displayio.TileGrid(
            sheet, pixel_shader=pal, width=cols, height=rows,
            tile_width=gw, tile_height=gh, default_tile=0)
        self._field = km_weather.RainField(random, cols, rows, glyphs=n)
        self._nolink = label.Label(terminalio.FONT, text=" no link ",
                                   color=0x000000, background_color=0xFFFFFF)
        self._nolink.anchor_point = (1.0, 1.0)
        self._nolink.anchored_position = (km_weather.SCREEN_W,
                                          km_weather.SCREEN_H)
        self._nolink.hidden = True
        # The tag belongs to the base state ("nolink" is rain *plus* a tag),
        # so it lives here and the marquee wipes over it like the rain.
        self._base_group = displayio.Group()
        self._base_group.append(self._rain_grid)
        self._base_group.append(self._nolink)

        # --- layer 1: bell wall ----------------------------------------
        self._wall_group = displayio.Group()
        self._wall_group.hidden = True
        self._wall_layout = None       # last-drawn layout list

        # --- layer 2: marquee ------------------------------------------
        self._marquee_group = displayio.Group()
        self._marquee_group.hidden = True
        self._marquee_epoch = None
        self._marquee_tile = None

        # --- layer 3: badges (topmost) ---------------------------------
        self._badges = displayio.Group()
        self._rec = label.Label(terminalio.FONT, text=_REC_TEXT,
                                color=0x000000, background_color=0xFFFFFF)
        self._rec.anchor_point = (1.0, 0.0)
        self._rec.anchored_position = (km_weather.SCREEN_W, 0)
        self._rec.hidden = True
        self._submap = label.Label(terminalio.FONT, text=" ",
                                   color=0x000000, background_color=0xFFFFFF)
        self._submap.anchor_point = (1.0, 0.0)
        self._submap.anchored_position = (_SUBMAP_RIGHT, 0)
        self._submap.hidden = True
        self._badges.append(self._rec)
        self._badges.append(self._submap)

        for layer in (self._base_group, self._wall_group,
                      self._marquee_group, self._badges):
            self.group.append(layer)
        display.root_group = self.group

        # state caches -- write hardware only on change
        self._weather = None
        self._flags = (None, None)
        self._submap_text = None
        self._clock = km_weather.FrameClock(FRAME_MS, ticks_ms(),
                                            ticks_add, ticks_diff)
        self._rain_acc = 0

    # ---- state ---------------------------------------------------------
    def set_weather(self, state):
        """Base state: "calm" | "ringing" | "nolink". Idempotent."""
        if state == self._weather:
            return
        self._weather = state
        ringing = state == "ringing"
        # Three visibility flips at once: suspend the panel so it cannot
        # scan out the moment where neither base nor wall is showing.
        self._display.auto_refresh = False
        try:
            _set_hidden(self._base_group, ringing)
            _set_hidden(self._wall_group, not ringing)
            _set_hidden(self._nolink, state != "nolink")
        finally:
            self._display.auto_refresh = True

    def set_bells(self, order):
        """Rebuild the bell wall from a bell_order list. Idempotent."""
        layout = km_weather.wall_layout(order)
        if layout == self._wall_layout:
            return
        self._wall_layout = layout
        # Rare change event: rebuild the layer's children with the panel's
        # auto-refresh suspended, so an async scan-out can never catch the
        # half-built (or empty) wall -- the 489c99a blank-frame bug class.
        self._display.auto_refresh = False
        try:
            while len(self._wall_group):
                self._wall_group.pop()
            for ws, size, x, y in layout:
                bank = self._big if size == "big" else self._small
                tg = displayio.TileGrid(bank[ws % 10],
                                        pixel_shader=self._pal, x=x, y=y)
                self._wall_group.append(tg)
        finally:
            self._display.auto_refresh = True

    def marquee(self, ws):
        """Start the workspace-switch wipe. Event-driven, not per-tick."""
        # Swap the numeral, park it fully off the right edge, and unhide --
        # three steps, so the panel is suspended across them. It starts at
        # x = SCREEN_W (entirely off screen), so unhiding shows nothing until
        # tick() first moves it; there is no flash at the wrong position.
        self._display.auto_refresh = False
        try:
            while len(self._marquee_group):
                self._marquee_group.pop()
            tg = displayio.TileGrid(
                self._big[ws % 10], pixel_shader=self._pal,
                x=km_weather.SCREEN_W,
                y=(km_weather.SCREEN_H - km_weather.BIG_H) // 2)
            self._marquee_group.append(tg)
            self._marquee_tile = tg
            self._marquee_group.hidden = False
        finally:
            self._display.auto_refresh = True
        self._marquee_epoch = ticks_ms()

    def set_flags(self, rec, submap):
        """REC block and [submap] badge. rec is bool, submap a str. Idempotent."""
        if (rec, submap) == self._flags:
            return
        self._flags = (rec, submap)
        # Submap yields on collision: " [name] " at 6px/char, right-anchored
        # at _SUBMAP_RIGHT, must stay on screen left of REC's reserved corner.
        # The anchor is fixed whether or not REC is showing, so the badge
        # never reflows -- it is simply dropped when it will not fit.
        # Label.text has no equality short-circuit (docs/pad-timing.md
        # section 5), so the text write goes through its own cache rather
        # than the flags tuple: two different flag tuples can want the same
        # badge text.
        text = " [" + submap + "] " if submap and len(submap) <= _SUBMAP_MAX \
            else ""
        self._display.auto_refresh = False
        try:
            _set_hidden(self._rec, not rec)
            if text != self._submap_text:
                self._submap_text = text
                if text:
                    # Only ever assign non-empty text; an empty Label is
                    # hidden instead, which costs no glyph rebuild.
                    self._submap.text = text
                _set_hidden(self._submap, not text)
        finally:
            self._display.auto_refresh = True

    # ---- animation ------------------------------------------------------
    def tick(self, now):
        # ALL display mutation below is gated behind the frame clock -- the
        # main loop is unthrottled (docs/pad-timing.md section 1), so
        # anything outside this gate would dirty the panel at loop frequency.
        frames = self._clock.advance(now)
        if not frames:
            return

        if self._marquee_epoch is not None:
            # marquee_x is a pure function of elapsed time, so a slow tick
            # skips ahead rather than stretching the wipe. x runs from
            # SCREEN_W down to -(BIG_W - 1); displayio clips a TileGrid that
            # hangs off either edge, so no write lands outside 128x64.
            x = km_weather.marquee_x(ticks_diff(now, self._marquee_epoch))
            if x is None:
                self._marquee_epoch = None
                _set_hidden(self._marquee_group, True)
            elif self._marquee_tile.x != x:
                self._marquee_tile.x = x

        # Rain runs at a whole-number division of the frame clock. On a
        # catch-up burst the accumulator resets rather than stepping the
        # field N times: dropping skipped rain frames is correct here (the
        # animation is decorative and a burst would read as a glitch),
        # whereas the marquee above must not lose position.
        self._rain_acc += frames
        if self._rain_acc >= RAIN_DIV:
            self._rain_acc = 0
            # A hidden base layer schedules no work at all, and a calm
            # screen whose field produced no changes writes nothing.
            if not self._base_group.hidden:
                for col, row, kind, glyph_i in self._field.step():
                    if kind == "off":
                        self._rain_grid[col, row] = 0
                    else:
                        bank = _KIND_BANK[kind] * self._rain_n
                        self._rain_grid[col, row] = 1 + bank + glyph_i
