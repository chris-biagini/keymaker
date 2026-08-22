"""Deck slot allocation and geometry. Pure: runs on CPython and CircuitPython.

Sticky allocation: a window claims the lowest free slot when it appears and holds
it until it closes. Nothing another window does can move it -- that is the whole
property, and the reason the deck is worth learning. Slots are a GLOBAL space;
paging changes which twelve are rendered, never which slot anything holds.
"""

SLOTS_PER_PAGE = 12


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
