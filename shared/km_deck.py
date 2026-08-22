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
        # Cold start (or any batch of new windows) is sorted so the assignment is
        # reproducible rather than dependent on enumeration order.
        fresh.sort(key=lambda x: (x["ws"], x["n"]))
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

    def message(self, page, knob, colors, focused=None, bells=(), name_max=14, ws_max=12):
        """The wire message for one page. See spec section 9.

        Workspaces are sent ONCE by reference and names trimmed: workspace names
        to ws_max (default 12) and window names to name_max (default 14). An
        over-long line is DISCARDED, not truncated -- an overflow would silently
        blank the pad. bells and map are bounded to MINIMAP_MAX_PAGES because the
        OLED screen can only draw that many page boxes; bells on undrawable pages
        and map entries beyond that point are pointless to send.
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
        return {
            "t": "deck", "page": page, "pages": pages, "knob": knob,
            "ws": [[n[:ws_max], colors.get(n, "ffffff")] for n in names],
            "slots": slots, "map": masks,
            "bells": sorted(s for s in (self.slots[w] for w in bells if w in self.slots) if s < MINIMAP_MAX_PAGES * SLOTS_PER_PAGE),
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
