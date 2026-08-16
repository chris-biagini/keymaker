"""Persist coach sessions to coach.json; derive state via km_coach."""
import json
import os
import time
from pathlib import Path

import km_coach


def _sanitize(session):
    if not isinstance(session, dict):
        return None
    out = dict(session)
    try:
        out["stage"] = int(out.get("stage"))
        out["score"] = float(out.get("score") or 0.0)
        out["duration_ms"] = int(out.get("duration_ms") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 <= out["stage"] <= 5:
        return None
    if not (0.0 <= out["score"] <= 1.0):
        return None
    return out


class CoachStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return []
            hist = data.get("history")
            return hist if isinstance(hist, list) else []
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            print(f"keymakerd: coach.json unreadable, starting empty: {e!r}",
                  flush=True)
            return []

    def append(self, session):
        entry = _sanitize(session)
        if entry is None:
            print(f"keymakerd: coach session rejected: {session!r}", flush=True)
            return
        hist = self.load()
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        hist.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"version": 1, "history": hist}, f)
        os.replace(tmp, self.path)

    def state_msg(self):
        try:
            return {"t": "coach", **km_coach.summarize(self.load())}
        except Exception as e:
            print(f"keymakerd: coach summarize failed: {e!r}", flush=True)
            return {"t": "coach", **km_coach.summarize([])}
