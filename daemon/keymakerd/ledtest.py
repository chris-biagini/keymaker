"""Debug LED bridge: watch a spool file, push a ledtest frame to the pad.

Spike-grade tooling for palette eyeballing (colorhash sub-project 1).
The daemon linearizes gamma-encoded hex to PWM ints so the pad stays dumb --
this deliberately supersedes the spec's `colors`-hex wire sketch, keeping all
float math on the host side of the link.

send is SerialLink.send: SYNCHRONOUS, returns bool -- never await it.
Every ValueError is caught here: this coroutine shares Supervisor.run()'s
gather, so an escaping exception kills the whole daemon.
"""
import asyncio
import json
import os

GAMMA = 2.2
N_KEYS = 12
HOLD_MIN, HOLD_MAX = 1, 300

# int(x, 16) is NOT a hex-digit check: it accepts signs and surrounding
# whitespace. int("-1", 16) == -1, and a negative base to a fractional power is
# complex -- round(complex) raises TypeError, which watch() does not catch and
# which therefore kills the daemon. "+1" and " f" are quieter but just as wrong,
# rendering a color nobody asked for. So validate the digits explicitly.
_HEX_DIGITS = "0123456789abcdefABCDEF"


def linearize(hex_str):
    """'#rrggbb' (gamma-encoded, as a designer picks it) -> PWM ints."""
    if not (isinstance(hex_str, str) and len(hex_str) == 7 and hex_str[0] == "#"
            and all(c in _HEX_DIGITS for c in hex_str[1:])):
        raise ValueError(f"bad hex {hex_str!r}")
    v = tuple(int(hex_str[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(round(255 * (c / 255) ** GAMMA) for c in v)


def parse_spool(text):
    """-> ([(r, g, b)] * 12, hold). Raises ValueError for EVERY bad shape.

    Wrong-type inputs must not surface as TypeError: the caller only guards
    ValueError, and anything else escaping the watcher takes the daemon down.
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid json: {e}")
    if not isinstance(doc, dict):
        raise ValueError("spool must be a JSON object")
    colors = doc.get("colors")
    if not (isinstance(colors, list) and len(colors) == N_KEYS):
        raise ValueError(f"colors must be exactly {N_KEYS} hex strings")
    hold = doc.get("hold")
    # bool is an int subclass; True would otherwise pass as hold=1.
    if not (isinstance(hold, int) and not isinstance(hold, bool)
            and HOLD_MIN <= hold <= HOLD_MAX):
        raise ValueError(f"hold must be an int in {HOLD_MIN}-{HOLD_MAX}")
    return [linearize(c) for c in colors], hold


async def watch(path, send, interval=1.0):
    """Poll the spool; on mtime change, parse and send (sync, returns bool).

    last starts at the CURRENT mtime so a pre-existing spool is treated as
    consumed (otherwise every daemon restart replays the last test frame).
    last only advances when send() reports success, so a frame written while
    the link is down is retried after reconnect.
    """
    try:
        last = os.stat(path).st_mtime
    except OSError:
        last = 0.0
    while True:
        try:
            st = os.stat(path)
            if st.st_mtime > last:
                with open(path) as f:
                    rgb, hold = parse_spool(f.read())
                frame = {"t": "ledtest", "rgb": [list(t) for t in rgb], "hold": hold}
                if send(frame):
                    last = st.st_mtime
                    # Logged because this is debug tooling with no other
                    # observable: the pad is the only feedback channel, and
                    # "did the frame leave the host?" is the first question
                    # when it looks wrong.
                    print(f"keymakerd: ledtest frame sent (hold={hold}s)", flush=True)
        except OSError:
            pass
        except ValueError as e:
            print(f"keymakerd: ledtest bad spool ignored: {e}", flush=True)
            last = st.st_mtime          # don't re-log the same bad file every tick
        await asyncio.sleep(interval)
