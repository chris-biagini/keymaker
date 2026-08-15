"""usb_cdc.data wrapper: JSON-lines in/out, ping/pong, link-up tracking."""
import usb_cdc

import km_proto

LINK_TIMEOUT_MS = 15000


class Link:
    def __init__(self, ticks_ms, ticks_diff):
        self.ser = usb_cdc.data
        self.codec = km_proto.LineCodec()
        self.ticks_ms = ticks_ms
        self.ticks_diff = ticks_diff
        self.last_rx = None

    @property
    def up(self):
        if self.ser is None or self.last_rx is None:
            return False
        return self.ticks_diff(self.ticks_ms(), self.last_rx) < LINK_TIMEOUT_MS

    def send(self, msg):
        if self.ser is None:
            return False
        try:
            self.ser.write(km_proto.encode(msg))
            return True
        except OSError:
            return False

    def poll(self, now):
        """Read pending bytes; answer pings; return app-relevant messages."""
        if self.ser is None or self.ser.in_waiting == 0:
            return []
        data = self.ser.read(self.ser.in_waiting)
        msgs = self.codec.feed(data)
        if msgs:
            self.last_rx = now
        out = []
        for m in msgs:
            if m["t"] == "ping":
                self.send({"t": "pong"})
            else:
                out.append(m)
        return out
