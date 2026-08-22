"""JSON-lines protocol codec. Pure: runs on CPython and CircuitPython."""
import json


def encode(msg):
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


class LineCodec:
    def __init__(self, max_line=2048):
        # 2048 bytes to accommodate a full twelve-slot deck message. A deck
        # message encodes to 982 bytes in the worst case (12 DISTINCT
        # workspaces -- a single repeated workspace is the BEST case, since
        # workspaces are sent once by reference -- every window ringing, and a
        # focused window), measured by
        # tests/test_deck.py::test_message_worst_case_wire_size_is_under_the_codec_cap.
        # `slots` carries JSON key overhead {"i":_,"c":_,"n":_,"s":_} twelve
        # times regardless of content. The cap has to clear the largest
        # legitimate message with room to spare. An over-long line is
        # DISCARDED, not truncated. RP2040 has 264 KB RAM, so a 2 KB buffer is
        # not a cost.
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
            if not line or len(line) > self._max:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and "t" in m:
                msgs.append(m)
