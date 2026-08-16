"""Persist coach sessions to coach.json; derive state via km_coach."""
import json
import os
import time
from pathlib import Path

import km_coach


class CoachStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            hist = data.get("history")
            return hist if isinstance(hist, list) else []
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            print(f"keymakerd: coach.json unreadable, starting empty: {e!r}",
                  flush=True)
            return []

    def append(self, session):
        hist = self.load()
        entry = dict(session)
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        hist.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"version": 1, "history": hist}, f)
        os.replace(tmp, self.path)

    def state_msg(self):
        return {"t": "coach", **km_coach.summarize(self.load())}
