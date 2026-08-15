"""Tap-vs-hold classification. Pure; time comes from the caller."""


class KeyTracker:
    def __init__(self, hold_ms=400, diff=None):
        self.hold_ms = hold_ms
        self.diff = diff or (lambda a, b: a - b)
        self._down = {}   # n -> [t0, hold_fired]

    def press(self, n, now):
        self._down[n] = [now, False]

    def release(self, n, now):
        rec = self._down.pop(n, None)
        if rec is None or rec[1]:
            return None
        return "tap"

    def tick(self, now):
        fired = []
        for n, rec in self._down.items():
            if not rec[1] and self.diff(now, rec[0]) >= self.hold_ms:
                rec[1] = True
                fired.append(n)
        return fired
