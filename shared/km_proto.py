"""JSON-lines protocol codec. Pure: runs on CPython and CircuitPython."""
import json


def encode(msg):
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


class LineCodec:
    def __init__(self, max_line=1024):
        self._buf = bytearray()
        self._max = max_line
        self._overflow = False

    def feed(self, data):
        msgs = []
        self._buf += data
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                if len(self._buf) > self._max:
                    self._buf = bytearray()
                    self._overflow = True   # discard until next newline
                return msgs
            line = bytes(self._buf[:i])
            self._buf = self._buf[i + 1:]
            if self._overflow:              # tail of an oversize line
                self._overflow = False
                continue
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and "t" in m:
                msgs.append(m)
