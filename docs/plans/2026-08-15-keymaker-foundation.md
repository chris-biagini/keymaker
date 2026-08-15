# Keymaker Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flash the MacroPad to CircuitPython 10.2.1 and build the working Cockpit end-to-end: firmware app framework + host daemon + JSON-lines protocol + Omarchy theme following + system plumbing.

**Architecture:** Smart device / peer daemon. CircuitPython firmware owns drawing, LEDs, input, and app switching; a host asyncio daemon (`keymakerd`) owns Hyprland IPC, Omarchy theme, volume, and actuation. They speak state-shaped JSON-lines over the `usb_cdc` data channel; each side works (degraded) without the other. Pure-logic modules live in `shared/` and run both on-device and under host pytest.

**Tech Stack:** CircuitPython 10.2.1 + `adafruit_macropad` (device); Python ≥3.11 stdlib + `pyserial` (host); pytest; Hyprland IPC sockets; `wpctl`; systemd user unit + udev.

**Spec:** `docs/specs/2026-08-15-keymaker-design.md` (this plan implements its milestones 1–2; the Coach is a follow-up plan).

## Global Constraints

- Daemon: Python ≥3.11, **stdlib + pyserial only** (host has Python 3.14.6; `python-pyserial` and `python-pytest` come from official pacman repos — **no AUR, ever**).
- Firmware: CircuitPython **10.2.1** exactly; bundle libs vendored in `firmware/lib/` (10.x-mpy).
- `shared/km_*.py` modules: **no CircuitPython imports, no CPython≥3.12-only syntax** — they must run on both runtimes. `tomllib`/`asyncio`/`pathlib` are daemon-only and never appear in `shared/`.
- Protocol: single-line JSON objects with a `"t"` field, `\n`-terminated, UTF-8.
- Omarchy contract (Quattro-proof): only `colors.toml` key names and the current-theme path pair (`~/.local/state/omarchy/current/theme` then `~/.config/omarchy/current/theme`). No hooks, no templates, no waybar/mako/playerctl, no `exec-once`, no `~/.local/share/omarchy` paths.
- Commit prefixes: `feat:` / `fix:` / `docs:` / `chore:`; imperative subjects <72 chars; run `pytest` before every commit from Task 3 onward.
- Device paths: only via udev symlinks `/dev/keymaker-repl` / `/dev/keymaker-data` (never bare `ttyACM*` in code).
- Physical steps (bootloader button dance, `sudo`) are Chris's; everything else is automated.

## File Structure

```
keymaker/
├── firmware/               # rsync'd to CIRCUITPY root by system/deploy-firmware.sh
│   ├── boot.py             # usb_cdc dual-channel enable (console + data)
│   ├── code.py             # entry point: init MacroPad, build apps, run framework
│   ├── lib/                # vendored 10.x-mpy bundle libs (committed)
│   ├── pad/                # device-only framework
│   │   ├── __init__.py
│   │   ├── link.py         # usb_cdc.data wrapper: poll/send, ping/pong, link-down
│   │   ├── framework.py    # main loop, app registry, long-press menu
│   │   └── ui.py           # displayio screen builders (header/title/footer, idle card)
│   └── apps/
│       ├── __init__.py
│       └── cockpit.py      # Cockpit app (workspace deck)
├── shared/                 # pure modules; copied to CIRCUITPY/lib by deploy script
│   ├── km_proto.py         # encode() + LineCodec (JSON-lines, garbage-tolerant)
│   ├── km_palette.py       # hex/int color math, cockpit key-state colors, pulse
│   ├── km_keys.py          # KeyTracker: tap-vs-hold state machine
│   └── km_text.py          # marquee() windowing function
├── daemon/
│   └── keymakerd/
│       ├── __init__.py
│       ├── __main__.py     # Config, supervisor: wires link+hypr+theme+volume+ping
│       ├── hyprland.py     # instance discovery, request(), event parse, HyprState
│       ├── theme.py        # theme-dir resolution, colors.toml → palette msg, watcher
│       ├── volume.py       # wpctl wrappers + output parser
│       └── serial_link.py  # pyserial + asyncio add_reader + reconnect loop
├── system/
│   ├── 99-keymaker.rules   # symlinks + ModemManager ignore (sudo install, Chris)
│   ├── keymaker.service    # systemd user unit
│   ├── deploy-firmware.sh  # rsync firmware→CIRCUITPY, shared→CIRCUITPY/lib
│   └── install.sh          # deploy + unit install/enable + udev instructions
└── tests/                  # pytest, runs entirely on host
    ├── test_proto.py
    ├── test_palette.py
    ├── test_keys.py
    ├── test_text.py
    ├── test_hyprland.py
    ├── test_theme.py
    ├── test_volume.py
    ├── test_serial_link.py
    └── test_supervisor.py
    └── fixtures/colors-fantasy.toml   # copied real theme file from nexus
```

Test invocation from repo root: `PYTHONPATH=shared:daemon pytest tests/ -v`. Codify it in `pytest.ini` (Task 3) so plain `pytest` works.

---

### Task 1: Flash CircuitPython 10.2.1, vendor libraries, skeleton firmware

Manual/hardware task — no TDD cycle. Chris performs the two physical steps.

**Files:**
- Create: `firmware/boot.py`, `firmware/code.py`, `firmware/lib/*` (vendored), `system/deploy-firmware.sh`, `.gitignore`

**Interfaces:**
- Produces: a pad running CP 10.2.1 exposing TWO CDC serials (console + data), drive `CIRCUITPY`; `system/deploy-firmware.sh` used by every later firmware task.

- [ ] **Step 1: Back up factory contents** (drive already mounts at `/run/media/chris/CIRCUITPY`)

```bash
cd ~/src/keymaker
printf 'backup-factory/\n__pycache__/\n.pytest_cache/\n' > .gitignore
mkdir -p backup-factory
cp -r /run/media/chris/CIRCUITPY/. backup-factory/
ls backup-factory   # expect boot_out.txt code.py lib macros
```

- [ ] **Step 2: Download firmware images**

```bash
mkdir -p /tmp/keymaker-flash && cd /tmp/keymaker-flash
curl -LO https://downloads.circuitpython.org/bin/adafruit_macropad_rp2040/en_US/adafruit-circuitpython-adafruit_macropad_rp2040-en_US-10.2.1.uf2
curl -LO https://datasheets.raspberrypi.com/soft/flash_nuke.uf2
ls -l   # both files present, uf2 ~1-2MB
```

- [ ] **Step 3 (CHRIS, hands): enter bootloader and nuke**
  1. Unmount: `udisksctl unmount -b /dev/disk/by-label/CIRCUITPY`
  2. **Hold the rotary knob down, tap the reset button on the back** (right edge, below the STEMMA QT port), release knob → drive `RPI-RP2` appears.
  3. `udisksctl mount -b /dev/disk/by-label/RPI-RP2 && cp /tmp/keymaker-flash/flash_nuke.uf2 /run/media/chris/RPI-RP2/`
  4. Wait ~10 s; `RPI-RP2` re-appears (auto-remounts after wipe).
  5. `udisksctl mount -b /dev/disk/by-label/RPI-RP2 2>/dev/null; cp /tmp/keymaker-flash/adafruit-circuitpython-*.uf2 /run/media/chris/RPI-RP2/`
  6. Pad reboots; `CIRCUITPY` appears.

- [ ] **Step 4: Verify version**

```bash
udisksctl mount -b /dev/disk/by-label/CIRCUITPY 2>/dev/null
cat /run/media/chris/CIRCUITPY/boot_out.txt
```
Expected: `Adafruit CircuitPython 10.2.1 on ...; Adafruit Macropad RP2040 with rp2040`

- [ ] **Step 5: Vendor bundle libraries into the repo**

```bash
cd /tmp/keymaker-flash
URL=$(gh api repos/adafruit/Adafruit_CircuitPython_Bundle/releases/latest \
  --jq '.assets[] | select(.name | test("bundle-10.x-mpy")) | .browser_download_url')
curl -LO "$URL" && unzip -q adafruit-circuitpython-bundle-10.x-mpy-*.zip
B=$(echo adafruit-circuitpython-bundle-10.x-mpy-*/lib)
mkdir -p ~/src/keymaker/firmware/lib
cp -r "$B"/adafruit_macropad.mpy "$B"/adafruit_debouncer.mpy "$B"/adafruit_ticks.mpy \
      "$B"/adafruit_simple_text_display.mpy "$B"/adafruit_pixelbuf.mpy "$B"/neopixel.mpy \
      "$B"/adafruit_hid "$B"/adafruit_midi "$B"/adafruit_display_text \
      "$B"/adafruit_display_shapes ~/src/keymaker/firmware/lib/
```

- [ ] **Step 6: Write `firmware/boot.py`**

```python
import usb_cdc

# Console (REPL) on the first CDC interface, data channel on the second.
# Takes effect on hard reset only (power cycle / reset button), not auto-reload.
usb_cdc.enable(console=True, data=True)
```

- [ ] **Step 7: Write skeleton `firmware/code.py`**

```python
import time
import usb_cdc

print("keymaker skeleton", "data channel:", usb_cdc.data is not None)
while True:
    time.sleep(1)
```

- [ ] **Step 8: Write `system/deploy-firmware.sh`**

```bash
#!/usr/bin/env bash
# Deploy firmware/ to the CIRCUITPY drive and shared/km_*.py into its lib/.
set -euo pipefail
cd "$(dirname "$0")/.."
MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY || true)
if [ -z "$MP" ]; then
    udisksctl mount -b /dev/disk/by-label/CIRCUITPY >/dev/null
    MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY)
fi
# 'P km_*.py' protects the shared modules (copied below) from --delete
rsync -r --delete --filter='P km_*.py' --exclude backup-factory firmware/ "$MP"/
cp shared/km_*.py "$MP/lib/" 2>/dev/null || true
sync
echo "deployed to $MP"
```
`chmod +x system/deploy-firmware.sh` (shared/ is empty until Task 3 — the `|| true` covers that).

- [ ] **Step 9: Deploy and hard-reset** — run `system/deploy-firmware.sh`; then Chris taps reset (boot.py needs a hard reset). Verify:

```bash
ls /dev/ttyACM*          # expect ttyACM0 AND ttyACM1
timeout 3 cat /dev/ttyACM0 || true   # console shows "keymaker skeleton data channel: True" after reload
```

- [ ] **Step 10: Commit**

```bash
cd ~/src/keymaker && git add -A
git commit -m "feat: CircuitPython 10.2.1 baseline, vendored libs, dual CDC, deploy script"
```

---

### Task 2: udev rule and host dependencies

**Files:**
- Create: `system/99-keymaker.rules`

**Interfaces:**
- Produces: stable `/dev/keymaker-repl` and `/dev/keymaker-data` symlinks; ModemManager ignores the pad; `pyserial`/`pytest` installed.

- [ ] **Step 1: Identify which interface is which** (console is the lower interface number)

```bash
udevadm info /dev/ttyACM0 | grep -E 'ID_USB_INTERFACE_NUM|DEVNAME'
udevadm info /dev/ttyACM1 | grep -E 'ID_USB_INTERFACE_NUM|DEVNAME'
```
Expected: ttyACM0 → `00` (console), ttyACM1 → a higher number, likely `02` (data). **If different, adjust the rule below to match observed numbers.**

- [ ] **Step 2: Write `system/99-keymaker.rules`**

```
# Adafruit MacroPad RP2040 (keymaker). Stable names + keep ModemManager away.
SUBSYSTEM=="tty", ATTRS{idVendor}=="239a", ATTRS{idProduct}=="8108", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="239a", ATTRS{idProduct}=="8108", ENV{ID_USB_INTERFACE_NUM}=="00", SYMLINK+="keymaker-repl"
SUBSYSTEM=="tty", ATTRS{idVendor}=="239a", ATTRS{idProduct}=="8108", ENV{ID_USB_INTERFACE_NUM}=="02", SYMLINK+="keymaker-data"
```

- [ ] **Step 3 (CHRIS, sudo): install rule + packages**

```bash
sudo cp ~/src/keymaker/system/99-keymaker.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty
sudo pacman -S --needed python-pyserial python-pytest
```

- [ ] **Step 4: Verify**

```bash
ls -l /dev/keymaker-repl /dev/keymaker-data   # both symlinks exist, point at ttyACM0/1
python -c "import serial; print(serial.__version__)"
```

- [ ] **Step 5: Commit**

```bash
cd ~/src/keymaker && git add system/99-keymaker.rules
git commit -m "feat: udev rule for stable keymaker device names"
```

---

### Task 3: Shared protocol codec (`km_proto`)

**Files:**
- Create: `shared/km_proto.py`, `tests/test_proto.py`, `pytest.ini`

**Interfaces:**
- Produces: `encode(msg: dict) -> bytes`; `class LineCodec: feed(data: bytes) -> list[dict]` (tolerates garbage, partial lines, oversize lines). Used by daemon `serial_link.py` and firmware `pad/link.py`.

- [ ] **Step 1: Write `pytest.ini`** (repo root)

```ini
[pytest]
testpaths = tests
pythonpath = shared daemon
```

- [ ] **Step 2: Write failing tests `tests/test_proto.py`**

```python
import km_proto


def test_encode_is_compact_jsonl():
    assert km_proto.encode({"t": "ping"}) == b'{"t":"ping"}\n'


def test_feed_single_message():
    c = km_proto.LineCodec()
    assert c.feed(b'{"t":"ws","active":3}\n') == [{"t": "ws", "active": 3}]


def test_feed_partial_then_rest():
    c = km_proto.LineCodec()
    assert c.feed(b'{"t":"pi') == []
    assert c.feed(b'ng"}\n{"t":"pong"}\n') == [{"t": "ping"}, {"t": "pong"}]


def test_feed_skips_garbage_and_non_dicts():
    c = km_proto.LineCodec()
    out = c.feed(b'not json\n[1,2]\n{"no_t":1}\n{"t":"ok"}\n\n')
    assert out == [{"t": "ok"}]


def test_oversize_line_dropped_and_recovers():
    c = km_proto.LineCodec(max_line=32)
    assert c.feed(b"x" * 100) == []
    assert c.feed(b'y\n{"t":"ok"}\n') == [{"t": "ok"}]
```

- [ ] **Step 3: Run to verify failure** — `pytest tests/test_proto.py -v` → FAIL (`ModuleNotFoundError: km_proto`)

- [ ] **Step 4: Write `shared/km_proto.py`**

```python
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
```

- [ ] **Step 5: Run to verify pass** — `pytest tests/test_proto.py -v` → 5 passed

- [ ] **Step 6: Commit** — `git add pytest.ini shared/km_proto.py tests/test_proto.py && git commit -m "feat: shared JSON-lines protocol codec"`

---

### Task 4: Shared palette math (`km_palette`)

**Files:**
- Create: `shared/km_palette.py`, `tests/test_palette.py`

**Interfaces:**
- Produces: `DEFAULT: dict` (palette with keys name/accent/bg/fg/red/muted as hex strings); `hex_to_int(s) -> int`; `scale(rgb: int, f: float) -> int`; `key_color(state: str, pal: dict, phase: float = 0.0) -> int` where state ∈ `"active" | "occupied" | "urgent" | "empty"`. Used by firmware `apps/cockpit.py`.

- [ ] **Step 1: Write failing tests `tests/test_palette.py`**

```python
import km_palette as kp


def test_hex_to_int_with_and_without_hash():
    assert kp.hex_to_int("#faa968") == 0xFAA968
    assert kp.hex_to_int("FAA968") == 0xFAA968


def test_scale_halves_channels():
    assert kp.scale(0x804020, 0.5) == 0x402010
    assert kp.scale(0xFFFFFF, 0.0) == 0x000000


def test_key_color_states():
    pal = {"accent": "FF0000", "red": "00FF00"}
    assert kp.key_color("active", pal) == 0xFF0000
    assert kp.key_color("occupied", pal) == kp.scale(0xFF0000, 0.12)
    assert kp.key_color("empty", pal) == 0
    dim = kp.key_color("urgent", pal, phase=0.0)
    bright = kp.key_color("urgent", pal, phase=1.0)
    assert bright == 0x00FF00 and 0 < dim < bright


def test_key_color_missing_keys_fall_back_to_default():
    assert kp.key_color("active", {}) == kp.hex_to_int(kp.DEFAULT["accent"])
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_palette.py -v` → FAIL

- [ ] **Step 3: Write `shared/km_palette.py`**

```python
"""Color math for the pad. Pure: no CircuitPython imports."""

DEFAULT = {
    "name": "default",
    "accent": "88CCFF", "bg": "111111", "fg": "DDDDDD",
    "red": "FF5555", "muted": "446677",
}


def hex_to_int(s):
    s = s.lstrip("#")
    return int(s[:6], 16)


def scale(rgb, f):
    r = int(((rgb >> 16) & 0xFF) * f)
    g = int(((rgb >> 8) & 0xFF) * f)
    b = int((rgb & 0xFF) * f)
    return (r << 16) | (g << 8) | b


def _c(pal, key):
    return hex_to_int(pal.get(key) or DEFAULT[key])


def key_color(state, pal, phase=0.0):
    if state == "active":
        return _c(pal, "accent")
    if state == "occupied":
        return scale(_c(pal, "accent"), 0.12)
    if state == "urgent":
        return scale(_c(pal, "red"), 0.25 + 0.75 * phase)
    return 0
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_palette.py -v` → 4 passed

- [ ] **Step 5: Commit** — `git add shared/km_palette.py tests/test_palette.py && git commit -m "feat: shared palette color math"`

---

### Task 5: Shared input + text helpers (`km_keys`, `km_text`)

**Files:**
- Create: `shared/km_keys.py`, `shared/km_text.py`, `tests/test_keys.py`, `tests/test_text.py`

**Interfaces:**
- Produces: `class KeyTracker(hold_ms=400, diff=None)` with `press(n, now)`, `release(n, now) -> "tap" | None`, `tick(now) -> list[int]` (key numbers whose hold just fired); `marquee(text, width, t_ms, cps=6, pause_ms=1500) -> str`. Used by `apps/cockpit.py` and `pad/framework.py`.

- [ ] **Step 1: Write failing tests `tests/test_keys.py`**

```python
from km_keys import KeyTracker


def test_short_press_is_tap():
    t = KeyTracker(hold_ms=400)
    t.press(3, 1000)
    assert t.tick(1100) == []
    assert t.release(3, 1200) == "tap"


def test_long_press_fires_hold_once_and_release_is_not_tap():
    t = KeyTracker(hold_ms=400)
    t.press(3, 1000)
    assert t.tick(1500) == [3]
    assert t.tick(1600) == []          # fires only once
    assert t.release(3, 1700) is None  # hold already consumed it


def test_release_without_press_is_none():
    assert KeyTracker().release(9, 50) is None


def test_custom_diff_supports_tick_wrap():
    # device passes adafruit_ticks.ticks_diff; emulate a wrapping counter
    t = KeyTracker(hold_ms=400, diff=lambda a, b: (a - b) % 2**16)
    t.press(1, 2**16 - 100)
    assert t.tick(350) == [1]          # wrapped: elapsed 450ms
```

- [ ] **Step 2: Write failing tests `tests/test_text.py`**

```python
from km_text import marquee


def test_short_text_unchanged():
    assert marquee("hi", 10, t_ms=999999) == "hi"


def test_starts_at_zero_during_lead_pause():
    assert marquee("abcdefghij", 5, t_ms=0) == "abcde"
    assert marquee("abcdefghij", 5, t_ms=1400) == "abcde"   # still in 1500ms pause


def test_scrolls_then_parks_at_end():
    s = "abcdefghij"                     # span = 5 positions
    mid = marquee(s, 5, t_ms=1500 + 2 * (1000 // 6))
    assert mid == s[2:7]
    end = marquee(s, 5, t_ms=1500 + 5 * (1000 // 6) + 100)
    assert end == "fghij"                # parked during tail pause
```

- [ ] **Step 3: Run to verify failure** — `pytest tests/test_keys.py tests/test_text.py -v` → FAIL

- [ ] **Step 4: Write `shared/km_keys.py`**

```python
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
```

- [ ] **Step 5: Write `shared/km_text.py`**

```python
"""Marquee windowing. Pure function of time; no state to corrupt."""


def marquee(text, width, t_ms, cps=6, pause_ms=1500):
    if len(text) <= width:
        return text
    span = len(text) - width
    step_ms = 1000 // cps
    cycle = pause_ms + span * step_ms + pause_ms
    t = t_ms % cycle
    if t < pause_ms:
        off = 0
    elif t < pause_ms + span * step_ms:
        off = (t - pause_ms) // step_ms
    else:
        off = span
    return text[off:off + width]
```

- [ ] **Step 6: Run to verify pass** — `pytest tests/test_keys.py tests/test_text.py -v` → 7 passed

- [ ] **Step 7: Commit** — `git add shared/km_keys.py shared/km_text.py tests/test_keys.py tests/test_text.py && git commit -m "feat: shared tap/hold tracker and marquee helpers"`

---

### Task 6: Daemon Hyprland module

**Files:**
- Create: `daemon/keymakerd/__init__.py` (empty), `daemon/keymakerd/hyprland.py`, `tests/test_hyprland.py`

**Interfaces:**
- Produces: `find_instance_dir(runtime: Path) -> Path | None`; `async request(instance_dir: Path, cmd: str) -> bytes` (one-shot on `.socket.sock`; JSON queries use `"j/..."` commands, actions use `"dispatch ..."`); `parse_event(line: str) -> tuple[str, str] | None`; `class HyprState` with `handle_event(name, data) -> tuple[bool, bool]` (needs_refresh, flags_changed), `refresh(workspaces, active_ws, active_win, clients) -> list[dict]` (changed `ws`/`win` msgs), `snapshot() -> list[dict]`, attributes `submap: str`, `screencast: bool`. Used by the Task 10 supervisor.

- [ ] **Step 1: Write failing tests `tests/test_hyprland.py`**

```python
from pathlib import Path

from keymakerd.hyprland import HyprState, find_instance_dir, parse_event

WORKSPACES = [
    {"id": 1, "windows": 2}, {"id": 2, "windows": 0}, {"id": 5, "windows": 1},
]
ACTIVE_WS = {"id": 1}
WIN = {"class": "foot", "title": "vim ~/notes.md"}
CLIENTS = [
    {"address": "0x5f2280", "workspace": {"id": 5}},
    {"address": "0x5f9000", "workspace": {"id": 1}},
]


def test_parse_event():
    assert parse_event("workspace>>3") == ("workspace", "3")
    assert parse_event("activewindow>>foot,a,b") == ("activewindow", "foot,a,b")
    assert parse_event("garbage") is None


def test_find_instance_dir_picks_dir_with_socket(tmp_path):
    a = tmp_path / "hypr" / "aaa"; a.mkdir(parents=True)
    b = tmp_path / "hypr" / "bbb"; b.mkdir(parents=True)
    (b / ".socket.sock").touch()
    assert find_instance_dir(tmp_path) == b


def test_refresh_produces_ws_and_win_messages_once():
    s = HyprState()
    msgs = s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert {"t": "ws", "active": 1, "occupied": [1, 5], "urgent": []} in msgs
    assert {"t": "win", "cls": "foot", "title": "vim ~/notes.md"} in msgs
    assert s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS) == []   # no change, no msgs


def test_urgent_event_address_is_normalized_and_cleared_on_focus():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    needs_refresh, _ = s.handle_event("urgent", "5f2280")   # event has NO 0x prefix
    assert needs_refresh
    msgs = s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    assert msgs[0]["urgent"] == [5]
    msgs = s.refresh(WORKSPACES, {"id": 5}, WIN, CLIENTS)   # focusing ws 5 clears it
    assert msgs[0]["urgent"] == []


def test_submap_and_screencast_touch_flags_only():
    s = HyprState()
    assert s.handle_event("submap", "resize") == (False, True)
    assert s.submap == "resize"
    assert s.handle_event("submap", "resize") == (False, False)
    assert s.handle_event("screencast", "1,0") == (False, True)
    assert s.screencast is True


def test_snapshot_always_returns_both_messages():
    s = HyprState()
    s.refresh(WORKSPACES, ACTIVE_WS, WIN, CLIENTS)
    ts = sorted(m["t"] for m in s.snapshot())
    assert ts == ["win", "ws"]
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_hyprland.py -v` → FAIL

- [ ] **Step 3: Write `daemon/keymakerd/hyprland.py`**

```python
"""Hyprland IPC: instance discovery, one-shot requests, event stream state."""
import asyncio
from pathlib import Path

REFRESH_EVENTS = {
    "workspace", "workspacev2", "focusedmon", "openwindow", "closewindow",
    "movewindow", "activewindow", "activewindowv2", "urgent", "fullscreen",
    "monitoradded", "monitorremoved",
}


def find_instance_dir(runtime):
    best = None
    for d in Path(runtime).glob("hypr/*"):
        if (d / ".socket.sock").exists():
            if best is None or d.stat().st_mtime > best.stat().st_mtime:
                best = d
    return best


async def request(instance_dir, cmd):
    reader, writer = await asyncio.open_unix_connection(str(instance_dir / ".socket.sock"))
    writer.write(cmd.encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    return data


def parse_event(line):
    if ">>" not in line:
        return None
    name, _, data = line.partition(">>")
    return name, data


class HyprState:
    def __init__(self):
        self.active = 1
        self.occupied = []
        self.urgent_ws = []
        self.cls = ""
        self.title = ""
        self.submap = ""
        self.screencast = False
        self._urgent_addrs = set()

    def handle_event(self, name, data):
        """Returns (needs_refresh, flags_changed)."""
        if name == "submap":
            changed = data != self.submap
            self.submap = data
            return False, changed
        if name == "screencast":
            on = data.split(",")[0] == "1"
            changed = on != self.screencast
            self.screencast = on
            return False, changed
        if name == "urgent":
            self._urgent_addrs.add(data.removeprefix("0x"))
            return True, False
        return name in REFRESH_EVENTS, False

    def refresh(self, workspaces, active_ws, active_win, clients):
        msgs = []
        active = active_ws.get("id", 1)
        occupied = sorted(w["id"] for w in workspaces if w.get("windows", 0) > 0)
        addr_ws = {
            str(c.get("address", "")).removeprefix("0x"): c.get("workspace", {}).get("id")
            for c in clients
        }
        self._urgent_addrs = {
            a for a in self._urgent_addrs
            if addr_ws.get(a) is not None and addr_ws[a] != active
        }
        urgent = sorted({addr_ws[a] for a in self._urgent_addrs})
        if (active, occupied, urgent) != (self.active, self.occupied, self.urgent_ws):
            self.active, self.occupied, self.urgent_ws = active, occupied, urgent
            msgs.append(self._ws_msg())
        cls = (active_win or {}).get("class", "")
        title = (active_win or {}).get("title", "")[:60]
        if (cls, title) != (self.cls, self.title):
            self.cls, self.title = cls, title
            msgs.append(self._win_msg())
        return msgs

    def snapshot(self):
        return [self._ws_msg(), self._win_msg()]

    def _ws_msg(self):
        return {"t": "ws", "active": self.active,
                "occupied": self.occupied, "urgent": self.urgent_ws}

    def _win_msg(self):
        return {"t": "win", "cls": self.cls, "title": self.title}
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_hyprland.py -v` → 6 passed

- [ ] **Step 5: Sanity-check against the live compositor** (read-only)

```bash
cd ~/src/keymaker && PYTHONPATH=daemon python - <<'PY'
import asyncio, json, os
from keymakerd.hyprland import find_instance_dir, request
d = find_instance_dir(os.environ["XDG_RUNTIME_DIR"])
ws = json.loads(asyncio.run(request(d, "j/workspaces")))
print("instance:", d.name, "workspaces:", [w["id"] for w in ws])
PY
```
Expected: real instance hash + current workspace ids.

- [ ] **Step 6: Commit** — `git add daemon tests/test_hyprland.py && git commit -m "feat: hyprland IPC module with pure state core"`

---

### Task 7: Daemon theme module

**Files:**
- Create: `daemon/keymakerd/theme.py`, `tests/test_theme.py`, `tests/fixtures/colors-fantasy.toml`

**Interfaces:**
- Produces: `resolve_theme_dir(home: Path) -> Path | None` (state path first, config fallback — THE Quattro rule); `load_palette(theme_dir: Path) -> dict | None` (palette msg: `t/name/accent/bg/fg/red/muted`, hex without `#`); `class ThemeWatcher(home, on_palette, poll_s=2.0)` with `async run()`. Used by the Task 10 supervisor.

- [ ] **Step 1: Copy the real theme file as a fixture**

```bash
mkdir -p ~/src/keymaker/tests/fixtures
cp "$(readlink -f ~/.config/omarchy/current/theme)/colors.toml" \
   ~/src/keymaker/tests/fixtures/colors-fantasy.toml
```

- [ ] **Step 2: Write failing tests `tests/test_theme.py`**

```python
from pathlib import Path

from keymakerd.theme import load_palette, resolve_theme_dir

FIXTURES = Path(__file__).parent / "fixtures"


def _theme(tmp_path, name, toml_text):
    d = tmp_path / name
    d.mkdir()
    (d / "colors.toml").write_text(toml_text)
    return d


def test_resolve_prefers_state_path_over_config(tmp_path):
    for rel in (".local/state/omarchy/current", ".config/omarchy/current"):
        target = tmp_path / rel / "themes" / rel.split("/")[1]
        target.mkdir(parents=True)
        (tmp_path / rel / "theme").symlink_to(target)
    assert "state" in str(resolve_theme_dir(tmp_path))


def test_resolve_falls_back_to_config_then_none(tmp_path):
    assert resolve_theme_dir(tmp_path) is None
    target = tmp_path / "t"; target.mkdir()
    cfg = tmp_path / ".config/omarchy/current"; cfg.mkdir(parents=True)
    (cfg / "theme").symlink_to(target)
    assert resolve_theme_dir(tmp_path) == target


def test_load_palette_from_real_omarchy_384_theme(tmp_path):
    d = tmp_path / "fantasy"; d.mkdir()
    (d / "colors.toml").write_text((FIXTURES / "colors-fantasy.toml").read_text())
    pal = load_palette(d)
    assert pal["t"] == "palette" and pal["name"] == "fantasy"
    assert pal["accent"] == "faa968" and pal["bg"] == "05182e"
    assert pal["red"] == "f85525"    # no 'red' key on 3.8.4 → color1 fallback
    assert pal["muted"] == "134e5a"  # no 'muted' key → color8 fallback


def test_load_palette_quattro_style_semantic_keys(tmp_path):
    d = _theme(tmp_path, "q", 'accent = "#112233"\nbackground = "#000000"\n'
               'foreground = "#ffffff"\nred = "#ff0000"\nmuted = "#333344"\n')
    pal = load_palette(d)
    assert pal["red"] == "ff0000" and pal["muted"] == "333344"


def test_load_palette_bad_or_missing_file(tmp_path):
    d = tmp_path / "x"; d.mkdir()
    assert load_palette(d) is None
    (d / "colors.toml").write_text("not [ valid toml")
    assert load_palette(d) is None
```

- [ ] **Step 3: Run to verify failure** — `pytest tests/test_theme.py -v` → FAIL

- [ ] **Step 4: Write `daemon/keymakerd/theme.py`**

```python
"""Omarchy theme → palette messages. Contract: colors.toml keys + theme path pair."""
import asyncio
import tomllib
from pathlib import Path

# Semantic keys with 3.8.4 ANSI fallbacks (observed on nexus: no red/muted keys).
_FALLBACKS = {
    "accent": ("accent",),
    "bg": ("background",),
    "fg": ("foreground",),
    "red": ("red", "color1"),
    "muted": ("muted", "color8"),
}


def resolve_theme_dir(home):
    home = Path(home)
    for p in (home / ".local/state/omarchy/current/theme",     # Omarchy 4.x
              home / ".config/omarchy/current/theme"):         # Omarchy 3.x
        if p.exists():
            return p.resolve()
    return None


def load_palette(theme_dir):
    f = Path(theme_dir) / "colors.toml"
    try:
        data = tomllib.loads(f.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    pal = {"t": "palette", "name": Path(theme_dir).name}
    for dst, keys in _FALLBACKS.items():
        for k in keys:
            v = data.get(k)
            if isinstance(v, str) and v:
                pal[dst] = v.lstrip("#").lower()
                break
    return pal if "accent" in pal else None


class ThemeWatcher:
    def __init__(self, home, on_palette, poll_s=2.0):
        self.home = home
        self.on_palette = on_palette
        self.poll_s = poll_s
        self._seen = None

    def check(self):
        """One poll step; returns the palette if it changed. Sync for testability."""
        d = resolve_theme_dir(self.home)
        if d is None:
            return None
        try:
            key = (str(d), (d / "colors.toml").stat().st_mtime_ns)
        except OSError:
            return None
        if key == self._seen:
            return None
        pal = load_palette(d)
        if pal is not None:
            self._seen = key
        return pal

    async def run(self):
        while True:
            pal = self.check()
            if pal is not None:
                await self.on_palette(pal)
            await asyncio.sleep(self.poll_s)
```

- [ ] **Step 5: Run to verify pass** — `pytest tests/test_theme.py -v` → 5 passed

- [ ] **Step 6: Commit** — `git add daemon/keymakerd/theme.py tests/test_theme.py tests/fixtures && git commit -m "feat: omarchy theme watcher with 3.x/4.x path and key fallbacks"`

---

### Task 8: Daemon volume module

**Files:**
- Create: `daemon/keymakerd/volume.py`, `tests/test_volume.py`

**Interfaces:**
- Produces: `parse_volume(out: str) -> tuple[float, bool]`; `async step(direction: int) -> None` (`+1`/`-1` → 5% up/down, capped 100%); `async toggle_mute() -> None`; `async status() -> tuple[float, bool]`. Used by the Task 10 supervisor (muted flows into the `flags` msg).

- [ ] **Step 1: Write failing tests `tests/test_volume.py`**

```python
import pytest

from keymakerd.volume import parse_volume


def test_parse_plain():
    assert parse_volume("Volume: 0.35\n") == (0.35, False)


def test_parse_muted():
    assert parse_volume("Volume: 0.35 [MUTED]\n") == (0.35, True)


def test_parse_garbage_raises():
    with pytest.raises((ValueError, IndexError)):
        parse_volume("wpctl exploded")
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_volume.py -v` → FAIL

- [ ] **Step 3: Write `daemon/keymakerd/volume.py`**

```python
"""PipeWire volume via wpctl subprocesses."""
import asyncio

SINK = "@DEFAULT_AUDIO_SINK@"


def parse_volume(out):
    parts = out.split()
    if len(parts) < 2 or parts[0] != "Volume:":
        raise ValueError(f"unexpected wpctl output: {out!r}")
    return float(parts[1]), out.rstrip().endswith("[MUTED]")


async def _run(*args):
    proc = await asyncio.create_subprocess_exec(
        "wpctl", *args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return out.decode()


async def step(direction):
    suffix = "5%+" if direction > 0 else "5%-"
    await _run("set-volume", "-l", "1.0", SINK, suffix)


async def toggle_mute():
    await _run("set-mute", SINK, "toggle")


async def status():
    return parse_volume(await _run("get-volume", SINK))
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_volume.py -v` → 3 passed. Live sanity check (safe, ±0 net):

```bash
cd ~/src/keymaker && PYTHONPATH=daemon python -c "
import asyncio; from keymakerd import volume
print(asyncio.run(volume.status()))"
```
Expected: `(0.35, False)`-shaped tuple matching `wpctl get-volume @DEFAULT_AUDIO_SINK@`.

- [ ] **Step 5: Commit** — `git add daemon/keymakerd/volume.py tests/test_volume.py && git commit -m "feat: wpctl volume wrapper"`

---

### Task 9: Daemon serial link

**Files:**
- Create: `daemon/keymakerd/serial_link.py`, `tests/test_serial_link.py`

**Interfaces:**
- Produces: `class SerialLink(path, on_msg, on_up, reconnect_s=2.0)` — `on_msg(msg: dict)` per inbound message, `on_up()` awaited on every (re)connect (supervisor sends the snapshot there); `send(msg: dict) -> bool` (False when link down; never raises); `async run()` (forever; reconnects on any serial error). Used by the Task 10 supervisor.

- [ ] **Step 1: Write failing tests `tests/test_serial_link.py`**

```python
import asyncio
import os

import pytest

import km_proto
from keymakerd.serial_link import SerialLink


@pytest.fixture
def pty_pair():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


async def _drain(master):
    await asyncio.sleep(0.15)
    try:
        return os.read(master, 4096)
    except BlockingIOError:
        return b""


def test_receives_messages_and_calls_on_up(pty_pair):
    master, slave_path = pty_pair
    got, ups = [], []

    async def scenario():
        link = SerialLink(slave_path, on_msg=got.append,
                         on_up=lambda: ups.append(1) or asyncio.sleep(0))
        task = asyncio.create_task(link.run())
        await asyncio.sleep(0.15)
        os.write(master, km_proto.encode({"t": "hello", "fw": "0.1.0"}))
        await asyncio.sleep(0.15)
        assert link.send({"t": "ping"}) is True
        out = await _drain(master)
        task.cancel()
        return out

    out = asyncio.run(scenario())
    assert got == [{"t": "hello", "fw": "0.1.0"}]
    assert ups == [1]
    assert b'{"t":"ping"}\n' in out


def test_send_while_down_returns_false():
    link = SerialLink("/dev/does-not-exist", on_msg=lambda m: None,
                     on_up=lambda: asyncio.sleep(0))
    assert link.send({"t": "ping"}) is False


def test_missing_device_keeps_retrying():
    async def scenario():
        link = SerialLink("/dev/does-not-exist", on_msg=lambda m: None,
                         on_up=lambda: asyncio.sleep(0), reconnect_s=0.05)
        task = asyncio.create_task(link.run())
        await asyncio.sleep(0.3)          # several failed attempts; no crash
        alive = not task.done()
        task.cancel()
        return alive

    assert asyncio.run(scenario()) is True
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_serial_link.py -v` → FAIL

- [ ] **Step 3: Write `daemon/keymakerd/serial_link.py`**

```python
"""Serial transport to the pad: pyserial + asyncio add_reader + reconnect."""
import asyncio

import serial

import km_proto


class SerialLink:
    def __init__(self, path, on_msg, on_up, reconnect_s=2.0):
        self.path = path
        self.on_msg = on_msg
        self.on_up = on_up
        self.reconnect_s = reconnect_s
        self._ser = None
        self._codec = km_proto.LineCodec()
        self._lost = None   # asyncio.Event while connected

    @property
    def up(self):
        return self._ser is not None

    def send(self, msg):
        if self._ser is None:
            return False
        try:
            self._ser.write(km_proto.encode(msg))
            return True
        except (serial.SerialException, OSError):
            self._drop()
            return False

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                self._ser = serial.Serial(self.path, 115200, timeout=0)
            except (serial.SerialException, OSError):
                self._ser = None
                await asyncio.sleep(self.reconnect_s)
                continue
            self._codec = km_proto.LineCodec()
            self._lost = asyncio.Event()
            loop.add_reader(self._ser.fileno(), self._readable)
            try:
                await self.on_up()
                await self._lost.wait()
            finally:
                self._drop()
            await asyncio.sleep(self.reconnect_s)

    def _readable(self):
        try:
            data = self._ser.read(4096)
        except (serial.SerialException, OSError, TypeError):
            self._drop()
            return
        if data == b"" :
            # pty EOF shows as readable-with-empty; real ttyACM raises instead
            self._drop()
            return
        for msg in self._codec.feed(data):
            self.on_msg(msg)

    def _drop(self):
        if self._ser is None:
            return
        try:
            asyncio.get_running_loop().remove_reader(self._ser.fileno())
        except (RuntimeError, OSError, ValueError):
            pass
        try:
            self._ser.close()
        except (serial.SerialException, OSError):
            pass
        self._ser = None
        if self._lost is not None:
            self._lost.set()
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_serial_link.py -v` → 3 passed (then full suite: `pytest -q`)

- [ ] **Step 5: Commit** — `git add daemon/keymakerd/serial_link.py tests/test_serial_link.py && git commit -m "feat: reconnecting serial link"`

---

### Task 10: Daemon supervisor (`__main__`)

**Files:**
- Create: `daemon/keymakerd/__main__.py`, `tests/test_supervisor.py`

**Interfaces:**
- Consumes: everything from Tasks 6–9 with the exact signatures above.
- Produces: `class Config(device: str, runtime_dir: Path, home: Path)`; `class Supervisor(cfg)` with `async run()`; module runnable as `python -m keymakerd` (env overrides `KEYMAKER_DEVICE`, default `/dev/keymaker-data`). Message behavior: on pad `hello`/link-up → send `hello`, `palette`, `ws`, `win`, `flags`; on `key` tap → `dispatch workspace N`; `key` hold → `dispatch movetoworkspacesilent N`; `dial` → volume step then `flags`; `click` → mute toggle then `flags`; `ping` every 5 s; refresh debounce 100 ms.

- [ ] **Step 1: Write failing integration test `tests/test_supervisor.py`** (fake Hyprland sockets + pty pad)

```python
import asyncio
import json
import os
from pathlib import Path

import pytest

import km_proto
from keymakerd.__main__ import Config, Supervisor

WORKSPACES = [{"id": 1, "windows": 1}, {"id": 3, "windows": 2}]


class FakeHypr:
    """Serves .socket.sock (requests) and .socket2.sock (events)."""

    def __init__(self):
        self.dispatched = []
        self.event_writer = None

    async def start(self, instance_dir):
        instance_dir.mkdir(parents=True)
        await asyncio.start_unix_server(self._req, path=str(instance_dir / ".socket.sock"))
        await asyncio.start_unix_server(self._ev, path=str(instance_dir / ".socket2.sock"))

    async def _req(self, reader, writer):
        cmd = (await reader.read(1024)).decode()
        if cmd == "j/workspaces":
            writer.write(json.dumps(WORKSPACES).encode())
        elif cmd == "j/activeworkspace":
            writer.write(b'{"id": 3}')
        elif cmd == "j/activewindow":
            writer.write(b'{"class": "foot", "title": "hello"}')
        elif cmd == "j/clients":
            writer.write(b"[]")
        else:
            self.dispatched.append(cmd)
            writer.write(b"ok")
        await writer.drain()
        writer.close()

    async def _ev(self, reader, writer):
        self.event_writer = writer


@pytest.fixture
def pad():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def _read_msgs(master):
    codec = km_proto.LineCodec()
    try:
        return codec.feed(os.read(master, 65536))
    except BlockingIOError:
        return []


def test_snapshot_on_connect_and_key_dispatch(pad, tmp_path):
    master, slave_path = pad

    async def scenario():
        hypr = FakeHypr()
        await hypr.start(tmp_path / "hypr" / "fake0")
        cfg = Config(device=slave_path, runtime_dir=tmp_path, home=tmp_path)
        sup = Supervisor(cfg)
        task = asyncio.create_task(sup.run())
        await asyncio.sleep(0.4)
        msgs = _read_msgs(master)
        # pad presses key 2 (0-based) → workspace 3, then holds key 0 → move to ws 1
        os.write(master, km_proto.encode({"t": "key", "n": 2, "act": "tap"}))
        os.write(master, km_proto.encode({"t": "key", "n": 0, "act": "hold"}))
        await asyncio.sleep(0.3)
        task.cancel()
        return msgs, hypr.dispatched

    msgs, dispatched = asyncio.run(scenario())
    types = [m["t"] for m in msgs]
    for expected in ("hello", "ws", "win", "flags"):
        assert expected in types
    # The link-up snapshot goes out before the first Hyprland refresh, so the
    # FIRST ws msg is the default state; the refreshed one arrives last.
    ws = [m for m in msgs if m["t"] == "ws"][-1]
    assert ws == {"t": "ws", "active": 3, "occupied": [1, 3], "urgent": []}
    assert "dispatch workspace 3" in dispatched
    assert "dispatch movetoworkspacesilent 1" in dispatched
```
(No palette assert: `tmp_path` has no omarchy dirs, so no palette is sent — the pad falls back to `km_palette.DEFAULT`. Theme flow is covered by Task 7 tests + live verification in Task 12.)

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_supervisor.py -v` → FAIL

- [ ] **Step 3: Write `daemon/keymakerd/__main__.py`**

```python
"""keymakerd: supervises serial link, Hyprland stream, theme watcher, volume."""
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import hyprland, volume
from .serial_link import SerialLink
from .theme import ThemeWatcher

PING_S = 5.0
DEBOUNCE_S = 0.1


@dataclass
class Config:
    device: str = os.environ.get("KEYMAKER_DEVICE", "/dev/keymaker-data")
    runtime_dir: Path = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
    home: Path = Path.home()


class Supervisor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = hyprland.HyprState()
        self.muted = False
        self.palette = None
        self.link = SerialLink(cfg.device, on_msg=self._on_pad_msg, on_up=self._on_link_up)
        self._refresh_wanted = asyncio.Event()
        self._instance = None

    # ---- outbound -------------------------------------------------
    def _flags_msg(self):
        return {"t": "flags", "submap": self.state.submap,
                "screencast": self.state.screencast, "muted": self.muted}

    async def _on_link_up(self):
        self.link.send({"t": "hello", "host": "keymakerd", "proto": 1})
        if self.palette:
            self.link.send(self.palette)
        for m in self.state.snapshot():
            self.link.send(m)
        self.link.send(self._flags_msg())

    async def _on_palette(self, pal):
        self.palette = pal
        self.link.send(pal)

    # ---- inbound from pad -----------------------------------------
    def _on_pad_msg(self, msg):
        t = msg.get("t")
        if t == "hello":
            asyncio.ensure_future(self._on_link_up())
        elif t == "key":
            n = int(msg.get("n", 0)) + 1                  # key 0 → workspace 1
            verb = "movetoworkspacesilent" if msg.get("act") == "hold" else "workspace"
            asyncio.ensure_future(self._dispatch(f"dispatch {verb} {n}"))
        elif t == "dial":
            asyncio.ensure_future(self._volume(int(msg.get("d", 0)), False))
        elif t == "click":
            asyncio.ensure_future(self._volume(0, True))

    async def _dispatch(self, cmd):
        if self._instance is not None:
            try:
                await hyprland.request(self._instance, cmd)
            except OSError:
                self._instance = None

    async def _volume(self, direction, toggle):
        if toggle:
            await volume.toggle_mute()
        elif direction:
            await volume.step(direction)
        try:
            _, self.muted = await volume.status()
        except (ValueError, IndexError):
            pass
        self.link.send(self._flags_msg())

    # ---- hyprland side --------------------------------------------
    async def _hypr_events(self):
        while True:
            self._instance = hyprland.find_instance_dir(self.cfg.runtime_dir)
            if self._instance is None:
                await asyncio.sleep(2)
                continue
            try:
                reader, _ = await asyncio.open_unix_connection(
                    str(self._instance / ".socket2.sock"))
                self._refresh_wanted.set()
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    ev = hyprland.parse_event(line.decode(errors="replace").rstrip("\n"))
                    if ev is None:
                        continue
                    needs_refresh, flags_changed = self.state.handle_event(*ev)
                    if needs_refresh:
                        self._refresh_wanted.set()
                    if flags_changed:
                        self.link.send(self._flags_msg())
            except OSError:
                pass
            await asyncio.sleep(2)   # hyprland restarting; rediscover

    async def _refresher(self):
        while True:
            await self._refresh_wanted.wait()
            await asyncio.sleep(DEBOUNCE_S)               # coalesce bursts
            self._refresh_wanted.clear()
            if self._instance is None:
                continue
            try:
                ws = json.loads(await hyprland.request(self._instance, "j/workspaces"))
                aw = json.loads(await hyprland.request(self._instance, "j/activeworkspace"))
                win_raw = await hyprland.request(self._instance, "j/activewindow")
                win = json.loads(win_raw) if win_raw.strip() not in (b"", b"{}") else None
                clients = json.loads(await hyprland.request(self._instance, "j/clients"))
            except (OSError, ValueError):
                continue
            for m in self.state.refresh(ws, aw, win, clients):
                self.link.send(m)

    async def _pinger(self):
        while True:
            await asyncio.sleep(PING_S)
            self.link.send({"t": "ping"})

    async def run(self):
        theme = ThemeWatcher(self.cfg.home, self._on_palette)
        await asyncio.gather(self.link.run(), self._hypr_events(),
                             self._refresher(), self._pinger(), theme.run())


def main():
    asyncio.run(Supervisor(Config()).run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_supervisor.py -v` → 1 passed; then the whole suite `pytest -q` → all green.

- [ ] **Step 5: Commit** — `git add daemon/keymakerd/__main__.py tests/test_supervisor.py && git commit -m "feat: keymakerd supervisor wiring link, hyprland, theme, volume"`

---

### Task 11: Firmware framework (`pad/`) + skeleton on hardware

**Files:**
- Create: `firmware/pad/__init__.py` (empty), `firmware/pad/link.py`, `firmware/pad/ui.py`, `firmware/pad/framework.py`, `firmware/apps/__init__.py` (empty)
- Modify: `firmware/code.py`

**Interfaces:**
- Consumes: `km_proto.LineCodec`/`encode`, `km_keys.KeyTracker`.
- Produces (for apps): `class App` base with `name`, and no-op `on_key_event(n, pressed, now)`, `on_dial(delta)`, `on_click()`, `on_msg(msg)`, `tick(now)`, `attach(pad, link, screen)`, `on_show()`; `Link.send(msg)`, `Link.up: bool`, `Link.poll(now) -> list[dict]`; `Screen` with `.header`, `.title`, `.footer` label attributes (set `.text` on them); `run(macropad, apps)` never returns. Encoder long-press (≥600 ms) opens the menu; menu owns input until click.

No pure logic here beyond what Tasks 3–5 already tested — this task is device glue, verified live on the pad (which is why it ends with a scripted hardware smoke test instead of pytest).

- [ ] **Step 1: Write `firmware/pad/link.py`**

```python
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
```

- [ ] **Step 2: Write `firmware/pad/ui.py`**

```python
"""OLED layout: three-line screen (header / title / footer) + idle card."""
import displayio
import terminalio
from adafruit_display_text import label

WIDTH_CHARS = 21   # 128px / 6px font


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        self.header = label.Label(terminalio.FONT, text="", x=0, y=6)
        self.title = label.Label(terminalio.FONT, text="", x=0, y=30)
        self.footer = label.Label(terminalio.FONT, text="", x=0, y=58)
        for l in (self.header, self.title, self.footer):
            self.group.append(l)
        display.root_group = self.group

    def idle_card(self):
        self.header.text = "keymaker"
        self.title.text = "no link"
        self.footer.text = ""
```

- [ ] **Step 3: Write `firmware/pad/framework.py`**

```python
"""Main loop: input polling, link polling, app switching via encoder long-press."""
from adafruit_ticks import ticks_diff, ticks_ms

from km_keys import KeyTracker

from .link import Link
from .ui import Screen

HOLD_MENU_MS = 600


class App:
    name = "app"

    def attach(self, pad, link, screen):
        self.pad = pad
        self.link = link
        self.screen = screen

    def on_show(self):
        pass

    def on_key_event(self, n, pressed, now):
        pass

    def on_dial(self, delta):
        pass

    def on_click(self):
        pass

    def on_msg(self, msg):
        pass

    def tick(self, now):
        pass


def run(macropad, apps):
    link = Link(ticks_ms, ticks_diff)
    screen = Screen(macropad.display)
    for app in apps:
        app.attach(macropad, link, screen)

    active = 0
    menu_idx = None            # not None → menu is open
    enc_tracker = KeyTracker(hold_ms=HOLD_MENU_MS, diff=ticks_diff)
    last_pos = macropad.encoder
    link.send({"t": "hello", "fw": "0.1.0", "app": apps[active].name})
    apps[active].on_show()

    while True:
        now = ticks_ms()

        macropad.encoder_switch_debounced.update()
        if macropad.encoder_switch_debounced.pressed:
            enc_tracker.press("enc", now)
        if macropad.encoder_switch_debounced.released:
            if enc_tracker.release("enc", now) == "tap":
                if menu_idx is not None:
                    active, menu_idx = menu_idx, None
                    link.send({"t": "hello", "fw": "0.1.0", "app": apps[active].name})
                    macropad.pixels.fill(0)
                    apps[active].on_show()
                else:
                    apps[active].on_click()
        if enc_tracker.tick(now):               # long press → open menu
            menu_idx = active

        pos = macropad.encoder
        delta, last_pos = pos - last_pos, pos
        if delta:
            if menu_idx is not None:
                menu_idx = (menu_idx + delta) % len(apps)
            else:
                apps[active].on_dial(delta)

        event = macropad.keys.events.get()
        while event is not None:
            if menu_idx is None:
                apps[active].on_key_event(event.key_number, event.pressed, now)
            event = macropad.keys.events.get()

        for m in link.poll(now):
            apps[active].on_msg(m)

        if menu_idx is not None:
            screen.header.text = "apps"
            screen.title.text = "> " + apps[menu_idx].name
            screen.footer.text = "click to switch"
        else:
            apps[active].tick(now)
```

- [ ] **Step 4: Write a temporary smoke app into `firmware/code.py`**

```python
import supervisor

from adafruit_macropad import MacroPad

from pad.framework import App, run


class Blink(App):
    name = "blink"

    def on_show(self):
        self.screen.idle_card()

    def on_key_event(self, n, pressed, now):
        self.pad.pixels[n] = 0x203040 if pressed else 0

    def on_msg(self, msg):
        self.screen.footer.text = msg.get("t", "?")


macropad = MacroPad()
supervisor.runtime.autoreload = True
run(macropad, [Blink()])
```

- [ ] **Step 5: Deploy and smoke-test on hardware**

```bash
cd ~/src/keymaker && ./system/deploy-firmware.sh
sleep 4
# console output (watch for tracebacks; Ctrl-C-safe because data is separate):
timeout 4 cat /dev/keymaker-repl || true
# ping the data channel and expect a pong (the boot-time hello was sent
# before we had the port open, so probe actively instead of listening):
timeout 6 python - <<'PY'
import serial, sys
s = serial.Serial("/dev/keymaker-data", 115200, timeout=5)
s.write(b'{"t":"ping"}\n')
line = s.readline()
print("RX:", line)
sys.exit(0 if b'"t":"pong"' in line else 1)
PY
```
Expected: no traceback on the console; `RX: b'{"t":"pong"}\n'`. Pressing keys lights them; knob long-press shows the `apps` menu.

- [ ] **Step 6: Commit** — `git add firmware/pad firmware/apps firmware/code.py && git commit -m "feat: firmware framework - link, screen, app switcher"`

---

### Task 12: Cockpit app + live end-to-end

**Files:**
- Create: `firmware/apps/cockpit.py`
- Modify: `firmware/code.py`

**Interfaces:**
- Consumes: `App`/`Screen`/`Link` (Task 11), `km_palette.key_color`, `km_keys.KeyTracker`, `km_text.marquee`; daemon messages `ws`/`win`/`flags`/`palette` and outbound `key`/`dial`/`click` (Task 10 shapes).

- [ ] **Step 1: Write `firmware/apps/cockpit.py`**

```python
"""Cockpit: 12 keys = Hyprland workspaces 1-12, knob = volume, OLED = focus."""
from adafruit_ticks import ticks_diff, ticks_ms

import km_palette
from km_keys import KeyTracker
from km_text import marquee

from pad.framework import App
from pad.ui import WIDTH_CHARS


class Cockpit(App):
    name = "cockpit"

    def __init__(self):
        self.ws = {"active": 1, "occupied": [], "urgent": []}
        self.win = {"cls": "", "title": ""}
        self.flags = {"submap": "", "screencast": False, "muted": False}
        self.palette = dict(km_palette.DEFAULT)
        self.tracker = KeyTracker(hold_ms=400, diff=ticks_diff)

    def on_show(self):
        self._draw_all(ticks_ms())

    def on_msg(self, msg):
        t = msg["t"]
        if t == "ws":
            self.ws = msg
        elif t == "win":
            self.win = msg
        elif t == "flags":
            self.flags = msg
        elif t == "palette":
            self.palette = msg
        self._draw_all(ticks_ms())

    def on_key_event(self, n, pressed, now):
        if pressed:
            self.tracker.press(n, now)
        elif self.tracker.release(n, now) == "tap":
            self.link.send({"t": "key", "n": n, "act": "tap"})

    def on_dial(self, delta):
        self.link.send({"t": "dial", "d": delta})

    def on_click(self):
        self.link.send({"t": "click"})

    def tick(self, now):
        for n in self.tracker.tick(now):
            self.link.send({"t": "key", "n": n, "act": "hold"})
        self._draw_leds(now)          # every pass: urgent pulse animation
        self._draw_text(now)          # marquee needs time too

    # ---- drawing --------------------------------------------------
    def _key_state(self, n):
        ws = n + 1
        if not self.link.up:
            return "empty"
        if ws == self.ws["active"]:
            return "active"
        if ws in self.ws["urgent"]:
            return "urgent"
        if ws in self.ws["occupied"]:
            return "occupied"
        return "empty"

    def _draw_leds(self, now):
        phase = (now % 1000) / 1000
        phase = phase * 2 if phase < 0.5 else (1 - phase) * 2   # triangle wave
        for n in range(12):
            self.pad.pixels[n] = km_palette.key_color(
                self._key_state(n), self.palette, phase)

    def _draw_text(self, now):
        if not self.link.up:
            self.screen.idle_card()
            return
        badges = ""
        if self.flags["screencast"]:
            badges += " REC"
        if self.flags["muted"]:
            badges += " MUTE"
        if self.flags["submap"]:
            badges += " [" + self.flags["submap"] + "]"
        self.screen.header.text = ("ws " + str(self.ws["active"]) + badges)[:WIDTH_CHARS]
        self.screen.title.text = marquee(self.win["title"], WIDTH_CHARS, now)
        self.screen.footer.text = self.win["cls"][:WIDTH_CHARS]

    def _draw_all(self, now):
        self._draw_leds(now)
        self._draw_text(now)
```

- [ ] **Step 2: Point `firmware/code.py` at Cockpit** (replace the Blink smoke app)

```python
import supervisor

from adafruit_macropad import MacroPad

from apps.cockpit import Cockpit
from pad.framework import run

macropad = MacroPad()
supervisor.runtime.autoreload = True
run(macropad, [Cockpit()])
```

- [ ] **Step 3: Deploy** — `./system/deploy-firmware.sh && sleep 4 && timeout 4 cat /dev/keymaker-repl || true` → no traceback; pad shows the idle card (no daemon yet).

- [ ] **Step 4: Run the daemon in the foreground**

```bash
cd ~/src/keymaker && PYTHONPATH=shared:daemon python -m keymakerd &
sleep 3
```

- [ ] **Step 5: Live acceptance checklist** (with Chris at the desk)
  - OLED shows `ws N` + focused window title/class; title longer than 21 chars scrolls.
  - Key colors: current workspace bright accent (`#faa968` on the current theme), occupied dim, empty dark.
  - Tap an occupied key → Hyprland jumps there; OLED and LEDs follow within ~200 ms.
  - Hold a key ≥400 ms → focused window moves to that workspace.
  - Knob → volume steps (verify `wpctl get-volume @DEFAULT_AUDIO_SINK@`); click → `MUTE` badge appears.
  - `omarchy-theme-set <other-theme>` → pad re-skins within ~2 s; set the original theme back.
  - Start a screen share/recording → `REC` badge; stop → gone.
  - Kill the daemon (`kill %1`) → pad falls to idle card ≤15 s; restart daemon → full state returns.
  - Unplug/replug USB → daemon reconnects by itself (watch its log).

- [ ] **Step 6: Commit** — `git add firmware/apps/cockpit.py firmware/code.py && git commit -m "feat: cockpit app - workspace deck, volume knob, status OLED"`

---

### Task 13: systemd unit, installer, docs

**Files:**
- Create: `system/keymaker.service`, `system/install.sh`
- Modify: `README.md` (only if observed behavior diverged from what it promises)

**Interfaces:**
- Consumes: `python -m keymakerd` (Task 10), `system/deploy-firmware.sh` (Task 1).
- Produces: `keymaker.service` running under the user session, surviving logout-free restarts; one-command install.

- [ ] **Step 1: Write `system/keymaker.service`**

```ini
[Unit]
Description=Keymaker macropad daemon
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=exec
ExecStart=/usr/bin/python -m keymakerd
Environment=PYTHONPATH=%h/src/keymaker/shared:%h/src/keymaker/daemon
Restart=always
RestartSec=2

[Install]
WantedBy=graphical-session.target
```

- [ ] **Step 2: Write `system/install.sh`**

```bash
#!/usr/bin/env bash
# Install keymaker on this machine: firmware to the pad, daemon to systemd.
set -euo pipefail
cd "$(dirname "$0")/.."

./system/deploy-firmware.sh

mkdir -p ~/.config/systemd/user
cp system/keymaker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now keymaker.service
systemctl --user --no-pager status keymaker.service || true

if [ ! -e /etc/udev/rules.d/99-keymaker.rules ]; then
    cat <<'EOF'

MANUAL STEP (root): install the udev rule, then replug the pad:
  sudo cp system/99-keymaker.rules /etc/udev/rules.d/
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty
EOF
fi
```
`chmod +x system/install.sh`.

- [ ] **Step 3: Install and verify persistence**

```bash
pkill -f "python -m keymakerd" 2>/dev/null || true   # stop any foreground copy
cd ~/src/keymaker && ./system/install.sh
systemctl --user is-active keymaker.service     # active
journalctl --user -u keymaker.service -n 20 --no-pager
# resilience: kill it, systemd revives it
systemctl --user kill keymaker.service && sleep 3
systemctl --user is-active keymaker.service     # active again; pad recovers
```

- [ ] **Step 4: Verify the README's install section matches reality**; fix if not.

- [ ] **Step 5: Commit and push**

```bash
cd ~/src/keymaker && git add -A
git commit -m "feat: systemd user unit and installer"
git push
```

- [ ] **Step 6: Document on the oracle side** — add a "Keymaker" section to `~/oracle/docs/setup.md` (device, repo path, udev rule location, service name, exact re-provision commands: `git clone` + `install.sh` + udev step + flash procedure reference), commit with `docs(setup):` and push. Add a `reference_keymaker` memory pointing at the repo and this plan, commit `chore(memory):`.

---

## Verification (after all tasks)

1. `pytest -q` → all green.
2. Full acceptance checklist from Task 12 Step 5, this time with the daemon under systemd.
3. Reboot test: reboot the box; daemon comes up with the session; pad shows live state without any manual action.
