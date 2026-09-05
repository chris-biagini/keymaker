# USB configuration. Runs once per hard reset (power cycle / reset button),
# before USB enumerates -- never on auto-reload, and never from code.py.
#
# Steady state hides the CIRCUITPY drive: it keeps the pad off the desktop and
# it removes the deploy hazard in docs/pad-timing.md section 7, where an rsync
# racing auto-reload remounted the FAT read-only and wedged USB for 30 s.
#
# Two ways back in, because a pad you cannot write to is a brick:
#   1. Hold key 1 (top-left) while plugging in / resetting.
#   2. Leave a marker file at /.expose-drive. This boot consumes it, so the
#      drive is exposed for exactly one boot. system/deploy-firmware.sh writes
#      it over the REPL, which is how an unattended deploy gets in.
# Beneath both: reset during the boot LED flash for safe mode (skips this file
# entirely), and BOOTSEL for the ROM bootloader. This file cannot brick the pad.
#
# Anything that raises here leaves the drive enabled -- the recoverable state.
# Keep disable_usb_drive() last for that reason.
import os
import time

import board
import digitalio
import storage
import usb_cdc

# Console (REPL) on the first CDC interface, data channel on the second.
# First, so the REPL survives any failure below.
usb_cdc.enable(console=True, data=True)

MARKER = "/.expose-drive"


def _escape_key_held():
    """True if key 1 is down right now. Keys are active-low with a pull-up."""
    key = digitalio.DigitalInOut(board.KEY1)
    try:
        key.switch_to_input(pull=digitalio.Pull.UP)
        # The pull-up needs a moment to charge the line. Reading immediately
        # can return the pre-pull value and report a released key as held,
        # which would leave the drive exposed on every boot.
        time.sleep(0.05)
        return not key.value
    finally:
        key.deinit()


def _consume_marker():
    """True if /.expose-drive was present. Deletes it, so it fires once.

    The root filesystem is read-only to CircuitPython by default, and the
    remount is only legal here in boot.py (before USB claims the drive) or at
    runtime while the drive is disabled. Restore read-only either way: if the
    host is about to get the drive, both sides writable corrupts the FAT.
    """
    try:
        os.stat(MARKER)
    except OSError:
        return False
    try:
        storage.remount("/", readonly=False)
        os.remove(MARKER)
    except (OSError, RuntimeError) as err:
        # Fails open: the marker survives, so the drive stays exposed on every
        # boot until it is cleared by hand. Visible, not silent.
        print("boot.py: could not clear", MARKER, "--", err)
    finally:
        try:
            storage.remount("/", readonly=True)
        except (OSError, RuntimeError):
            pass
    return True


expose = _consume_marker()
if _escape_key_held():
    print("boot.py: key 1 held, exposing CIRCUITPY")
    expose = True

if not expose:
    storage.disable_usb_drive()
