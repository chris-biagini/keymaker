"""Persist the deck's {window_id: slot} map. Mirrors coach_store.py's discipline:
never raise, atomic replace, validate on load.

Without this a `systemctl --user restart keymaker` reshuffles every key, which
destroys the exact property sticky allocation exists to provide.
"""
import json
import os
from pathlib import Path

MAX_SLOT = 999          # a sane ceiling; slots are dense from 0 in practice


class DeckStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            print(f"keymakerd: deck-slots.json unreadable, starting empty: {e!r}",
                  flush=True)
            return {}
        raw = data.get("slots") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return {}
        out = {}
        seen_slots = set()
        for wid, slot in raw.items():
            if not (isinstance(wid, str) and isinstance(slot, int)
                    and not isinstance(slot, bool) and 0 <= slot <= MAX_SLOT):
                continue
            # Two ids mapping to the same slot survives naive validation, and
            # Deck.message then silently drops one -- a live window with no
            # key for its whole life. Keep the first, drop the rest (dict
            # iteration order is JSON key order, so "first" is well-defined).
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            out[wid] = slot
        return out

    def save(self, slots):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump({"version": 1, "slots": slots}, f)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"keymakerd: deck-slots.json unwritable: {e!r}", flush=True)
