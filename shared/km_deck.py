"""Deck slot allocation and geometry. Pure: runs on CPython and CircuitPython.

Sticky allocation: a window claims the lowest free slot when it appears and holds
it until it closes. Nothing another window does can move it -- that is the whole
property, and the reason the deck is worth learning. Slots are a GLOBAL space;
paging changes which twelve are rendered, never which slot anything holds.
"""

SLOTS_PER_PAGE = 12
MINIMAP_MAX_PAGES = 5  # Five 15px boxes on a 19px pitch span 92px of the 128px
                       # screen, leaving ~36px for the counts text. A sixth box
                       # cannot be drawn, so bells/map entries beyond this page
                       # count are pointless to send.
FOCUS_MAX = 21         # OLED screen column count


class Deck:
    def __init__(self, slots=None):
        # {window_id: slot}. Restored from disk at startup; see deck_store.
        self.slots = dict(slots or {})
        # {slot: {"ws":..., "n":...}} for windows that have closed. A ghost
        # asserts "this finished while you were watching", so a restore must
        # never fabricate one -- self.ghosts starts empty even with slots given.
        self.ghosts = {}
        # Last-seen {ws, n} per window id, so a window that closes can leave a
        # correctly-labelled ghost behind. message() reads it for live slots too.
        self._last = {}

    def _free_slot(self):
        taken = set(self.slots.values())
        i = 0
        while i in taken:
            i += 1
        return i

    def update(self, windows):
        """Reconcile against the current window list. Mutates in place."""
        live = {}
        fresh = []
        for win in windows:
            if win["id"] in self.slots:
                live[win["id"]] = self.slots[win["id"]]
            else:
                fresh.append(win)
        # `fresh` keeps the caller's order verbatim -- no sort here. Slot
        # assignment order is a display-order decision (it determines which
        # key a window lands on), and only the caller knows what that order
        # should be (e.g. Hyprland's on-screen workspace order). km_deck stays
        # free of Hyprland concepts, so it trusts the input order rather than
        # imposing one of its own.
        gone = {wid: s for wid, s in self.slots.items() if wid not in live}
        by_id = {x["id"]: x for x in windows}
        self.slots = live
        for wid, slot in gone.items():
            prev = self._last.get(wid)
            if prev is not None:
                self.ghosts[slot] = prev
        for win in fresh:
            slot = self._free_slot()
            self.slots[win["id"]] = slot
            self.ghosts.pop(slot, None)     # claiming overwrites the ghost
        self._last = {x["id"]: {"ws": x["ws"], "n": x["n"]} for x in by_id.values()}

    def dismiss(self, slot):
        """Acknowledge a ghost. True if one was there. Never launches anything."""
        return self.ghosts.pop(slot, None) is not None

    def page_count(self):
        used = list(self.slots.values()) + list(self.ghosts.keys())
        top = max(used) + 1 if used else 0
        return max(1, (top + SLOTS_PER_PAGE - 1) // SLOTS_PER_PAGE)

    def message(self, page, colors, focused=None, bells=(), name_max=14, ws_max=12):
        """The wire message for one page. See spec section 9.

        Workspaces are sent ONCE by reference and names trimmed: workspace names
        to ws_max (default 12) and window names to name_max (default 14). An
        over-long line is DISCARDED, not truncated -- an overflow would silently
        blank the pad. bells and map are bounded to MINIMAP_MAX_PAGES because the
        OLED screen can only draw that many page boxes; bells on undrawable pages
        and map entries beyond that point are pointless to send. The knob only
        pages now, so no mode is carried here.
        """
        bells = set(bells)
        lo = page * SLOTS_PER_PAGE
        by_slot = {}
        for wid, slot in self.slots.items():
            state = "focused" if wid == focused else ("bell" if wid in bells else "live")
            by_slot[slot] = (self._last[wid], state)
        for slot, meta in self.ghosts.items():
            by_slot.setdefault(slot, (meta, "ghost"))

        names, slots = [], []
        for slot in sorted(by_slot):
            if not lo <= slot < lo + SLOTS_PER_PAGE:
                continue
            meta, state = by_slot[slot]
            if meta["ws"] not in names:
                names.append(meta["ws"])
            slots.append({"i": slot - lo, "c": names.index(meta["ws"]),
                          "n": meta["n"][:name_max], "s": state})

        # `map` is a per-page BITMASK, not a count: spec 8.2 says "filled cell =
        # slot occupied", and a count draws the first N cells of a page while
        # bells draw at the slot's ACTUAL position -- disagreeing wherever a
        # page is sparse (a dismissed mid-page ghost, a daemon restart that
        # drops dead windows without ghosts), which is routine, not rare.
        pages = self.page_count()
        masks = [0] * min(pages, MINIMAP_MAX_PAGES)
        for slot in by_slot:
            page_idx = slot // SLOTS_PER_PAGE
            if page_idx < MINIMAP_MAX_PAGES:
                masks[page_idx] |= 1 << (slot % SLOTS_PER_PAGE)

        # Composed here from _last rather than from `slots`, because the focused
        # window may be on a page the user is not looking at -- which is exactly
        # when naming it is most useful. _last holds every window regardless of
        # page, so this costs nothing extra.
        fmeta = self._last.get(focused) if focused else None
        focus = ("%s %s" % (fmeta["ws"], fmeta["n"]))[:FOCUS_MAX] if fmeta else ""

        return {
            "t": "deck", "page": page, "pages": pages, "total": len(self.slots),
            "ws": [[n[:ws_max], colors.get(n, "ffffff")] for n in names],
            "slots": slots, "map": masks,
            "bells": sorted(s for s in (self.slots[w] for w in bells if w in self.slots) if s < MINIMAP_MAX_PAGES * SLOTS_PER_PAGE),
            "focus": focus,
        }


# ---- OLED minimap geometry (spec section 8.2) -------------------------------
# Here rather than in firmware/pad/ui.py so it is unit-testable: CircuitPython
# does not run under pytest, so any arithmetic left in the firmware is arithmetic
# nobody can check.

def minimap_boxes(pages, x=1, y=38, w=15, h=20, gap=4):
    """One box per page, laid left to right, as (x, y, w, h)."""
    return [(x + p * (w + gap), y, w, h) for p in range(pages)]


def minimap_cell(slot, box, pitch=4, pad=2):
    """Top-left pixel of a slot's cell within its page box."""
    i = slot % SLOTS_PER_PAGE
    return (box[0] + pad + (i % 3) * pitch, box[1] + pad + 1 + (i // 3) * pitch)


def minimap_pixels(pages, page, masks, bells, blink, y=0):
    """Every lit pixel of the minimap, as a set of (x, y), bitmap-local.

    The whole frame, computed rather than drawn, so the caller can paint the
    DIFFERENCE against the previous frame instead of clearing and repainting.
    That matters more than it looks: displayio's auto_refresh is on (nothing in
    this firmware turns it off), so the panel scans out whenever it likes, and a
    fill(0)-then-redraw leaves a genuinely blank bitmap for it to catch. The
    blank frame is what reads as a flicker -- not the redraw cost.

    `masks` is a per-page bitmask (Deck.message's `map`): bit i set means slot i
    of that page is occupied. A cell is drawn only for a slot the mask actually
    claims -- matching bells, which draw at their real global slot -- so a sparse
    page (a dismissed mid-page ghost, a daemon restart that drops windows without
    ghosts) never shows a filled cell where nothing lives.
    """
    lit = set()
    boxes = minimap_boxes(pages, y=y)
    for p, box in enumerate(boxes):
        # `page` can exceed the drawable range: the keys reach every page, the
        # minimap only shows the first MINIMAP_MAX_PAGES. When the user is past
        # that, nothing is outlined and the header's "P7/9" carries the position
        # instead. Deliberate -- an outline on the wrong box would lie.
        if p == page:                       # outline the page on the keys
            bx, by, w, h = box
            for dx in range(w):
                lit.add((bx + dx, by))
                lit.add((bx + dx, by + h - 1))
            for dy in range(h):
                lit.add((bx, by + dy))
                lit.add((bx + w - 1, by + dy))
        mask = masks[p] if p < len(masks) else 0
        for i in range(SLOTS_PER_PAGE):
            if not mask >> i & 1:
                continue
            cx, cy = minimap_cell(p * SLOTS_PER_PAGE + i, box)
            for dx in range(2):
                for dy in range(2):
                    lit.add((cx + dx, cy + dy))
    # Bells draw larger (3x3 against a plain cell's 2x2), so an alert reads as
    # different in shape and not only in blink phase.
    if blink:
        for gslot in bells:
            p = gslot // SLOTS_PER_PAGE
            if p >= len(boxes):
                continue
            cx, cy = minimap_cell(gslot, boxes[p])
            for dx in range(3):
                for dy in range(3):
                    lit.add((cx + dx, cy + dy))
    return lit


# ---- OLED legend geometry (spec section 5.3-5.5) ---------------------------
# Here rather than in firmware/pad/ui.py so it is unit-testable: CircuitPython
# does not run under pytest, so any arithmetic left in the firmware is
# arithmetic nobody can check.

CELL_CHARS = 6          # "sss<sep>nn"
LEGEND_COLS = 3
LEGEND_ROWS = 4
CELL_SEP = ":"          # ASCII on purpose -- see the plan's ruling
GUTTER_W = 4
GUTTER_H = 9
GUTTER_PITCH_X = 42     # exactly seven 6px character cells
GUTTER_PITCH_Y = 10
GUTTER_X0 = 1


def _alnum2(s):
    """First two alphanumerics of `s`, right-padded to two.

    str.isalnum() is absent on CircuitPython, so the class test is explicit.
    """
    out = ""
    for c in s:
        if ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9"):
            out += c
            if len(out) == 2:
                break
    return (out + "  ")[:2]


def cell_label(ws, name):
    """One legend cell: three of the workspace, a separator, two of the window.

    Always exactly CELL_CHARS characters -- the legend row packs three cells at a
    fixed 7-character pitch, so a short label would shift every column right of
    it. Window names arrive as "<index> <name>", so the first two alphanumerics
    are the tmux index plus one letter, which is the most distinguishing thing
    about two windows in the same session.
    """
    return (ws[:3] + "   ")[:3] + CELL_SEP + _alnum2(name)


def legend_row(labels, row):
    """One 20-character legend row: three cells separated by single spaces.

    Character 6 and character 13 are always those spaces. They overlap the next
    column's gutter on screen, which is harmless precisely because they are blank.
    """
    out = []
    for col in range(LEGEND_COLS):
        i = row * LEGEND_COLS + col
        out.append(labels[i] if i < len(labels) else " " * CELL_CHARS)
    return " ".join(out)


def gutter_pixels(states, blink, y0=0):
    """Lit pixels of all twelve state gutters, bitmap-local, as a set of (x, y).

    Five states told apart by SHAPE, not brightness: a one-bit panel has no hue
    to spare and no intensity either, so shape is the only channel left.

    Returned as a whole frame so the caller can paint the DIFFERENCE against the
    previous frame. Never clear and repaint: displayio's auto_refresh is on, and
    a cleared bitmap is a blank frame the panel can scan out. See
    docs/pad-timing.md section 5.
    """
    lit = set()
    for i, st in enumerate(states[:LEGEND_COLS * LEGEND_ROWS]):
        x = GUTTER_X0 + (i % LEGEND_COLS) * GUTTER_PITCH_X
        y = y0 + (i // LEGEND_COLS) * GUTTER_PITCH_Y
        if st == "live":
            for dy in range(GUTTER_H):
                lit.add((x, y + dy))
        elif st == "ghost":
            for dy in range(0, GUTTER_H, 2):
                lit.add((x, y + dy))
        elif st == "focused":
            for dx in range(GUTTER_W):
                lit.add((x + dx, y))
                lit.add((x + dx, y + GUTTER_H - 1))
            for dy in range(GUTTER_H):
                lit.add((x, y + dy))
                lit.add((x + GUTTER_W - 1, y + dy))
        elif st == "bell" and blink:
            for dx in range(GUTTER_W):
                for dy in range(GUTTER_H):
                    lit.add((x + dx, y + dy))
    return lit


REKEY_START_MS = 500     # below this a hold shows nothing: a brush is not intent
REKEY_STEP_MS = 1000
REKEY_FIRE_MS = 3500


def countdown_text(elapsed_ms):
    """Focused-row text during a re-key hold, or None when the row is unchanged.

    Hold-to-confirm rather than a plain long-press: re-keying reorders the whole
    board, and sticky allocation means there is no undo.
    """
    if elapsed_ms < REKEY_START_MS:
        return None
    if elapsed_ms >= REKEY_FIRE_MS:
        return "RE-KEYING"
    return "RE-KEY IN %d" % (3 - (elapsed_ms - REKEY_START_MS) // REKEY_STEP_MS)
