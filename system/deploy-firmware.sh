#!/usr/bin/env bash
# Deploy firmware/ to the CIRCUITPY drive and shared/km_*.py into its lib/.
#
# CircuitPython's auto-reload restarts the pad ~1 s after the first write
# lands, which can re-enumerate USB mid-rsync (bench 2026-08-16: interrupted
# renames -> kernel remounted the FAT read-only -> 30 s USB reset loop).
# So: pause auto-reload over the REPL channel, copy everything, then trigger
# exactly one clean reload (supervisor.runtime.autoreload is not persistent,
# so the reload itself restores it).
set -euo pipefail
cd "$(dirname "$0")/.."

REPL=/dev/keymaker-repl
repl_ok=0
if [ -e "$REPL" ]; then
    python3 - "$REPL" <<'PY' && repl_ok=1 || echo "WARN: REPL pause failed; deploying with autoreload live"
import sys
import time

import serial

with serial.Serial(sys.argv[1], 115200, timeout=2) as s:
    s.write(b"\x03")          # Ctrl-C: stop code.py
    time.sleep(0.5)
    s.write(b"\r")            # any key: enter the REPL
    time.sleep(0.5)
    s.write(b"import supervisor; supervisor.runtime.autoreload = False\r")
    time.sleep(0.5)
    s.reset_input_buffer()
PY
else
    echo "WARN: $REPL missing; deploying with autoreload live"
fi

MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY || true)
if [ -z "$MP" ]; then
    udisksctl mount -b /dev/disk/by-label/CIRCUITPY >/dev/null
    MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY)
fi
# A previously interrupted deploy can leave the FAT remounted read-only.
if ! touch "$MP/.deploy-probe" 2>/dev/null; then
    echo "CIRCUITPY mount is read-only; remounting"
    udisksctl unmount -b /dev/disk/by-label/CIRCUITPY
    udisksctl mount -b /dev/disk/by-label/CIRCUITPY >/dev/null
    MP=$(findmnt -n -o TARGET LABEL=CIRCUITPY)
fi
rm -f "$MP/.deploy-probe"

# 'P km_*.py' protects the shared modules (synced below) from --delete: they
# don't live under firmware/, so without protection this pass would see them
# as absent from the source and delete them before the next line gets a
# chance to put them back.
rsync -r --delete --filter='P km_*.py' --exclude backup-factory \
    --exclude __pycache__ firmware/ "$MP"/
# A plain `cp` here only ever ADDS or updates files -- it can't remove a
# shared module that was deleted from shared/, so a retired km_*.py (e.g.
# km_coach.py) would survive on CIRCUITPY forever. Scope --delete to exactly
# the km_*.py pattern so this pass only ever touches the files it owns and
# leaves the rest of lib/ (the third-party adafruit_* packages) alone.
rsync -r --delete --include='km_*.py' --exclude='*' \
    --exclude __pycache__ shared/ "$MP/lib/"
sync

if [ "$repl_ok" = 1 ]; then
    python3 - "$REPL" <<'PY' || echo "WARN: reload trigger failed; press the pad's reset button once"
import sys

import serial

with serial.Serial(sys.argv[1], 115200, timeout=2) as s:
    s.write(b"\x04")          # Ctrl-D: one clean reload
PY
else
    echo "NOTE: autoreload was live during this deploy; if the pad wedged, replug it"
fi
echo "deployed to $MP"
