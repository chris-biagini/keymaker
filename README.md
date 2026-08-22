# keymaker

CircuitPython firmware and a small host daemon that turn an
[Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) into a desk
companion for an [Omarchy](https://omarchy.org) / Hyprland desktop: a physical
switchboard of open terminal windows, dressed in whatever Omarchy theme is
active.

Named for the Matrix's Keymaker: a pad of keys that opens doors.

## What it does

A single app: **Cockpit**, the switchboard.

The 3×4 key grid is a set of twelve sticky slots, one per open terminal
window across every Hyprland workspace, paged with the knob when there are
more than twelve. A window keeps its slot for life — sticky allocation means
nothing another window does can move it — so keys don't reshuffle underfoot.
Hold the encoder for 3.5 s to re-key the board from scratch.

- Each key is color-identified to its workspace (same palette as the tmux
  status bar). Tap a key to jump to that window.
- The OLED shows a four-row, twelve-cell legend naming every slot on the
  current page (`sss:nn` — session + window), with a per-cell state gutter
  (live, ghost, focused, bell) drawn as shape rather than color, since a
  1-bit panel has no hue or brightness to spare. The header shows the host,
  page, and window count; the row below it names whichever window is
  currently focused, scrolling if it doesn't fit.
- Urgency runs BEL → tmux → foot → Hyprland → pad, entirely in-band, so it
  works the same over mosh as it does locally.
- The knob only pages — there is no other mode, and no on-device app menu.

### Theme following

The daemon watches Omarchy's active theme (`colors.toml`) and pushes the
palette to the pad, which re-skins keys and screens within a couple of
seconds of a theme switch. No hooks, no templates — one file, one path, both
stable across Omarchy 3.x and 4.x.

## How it works

Two programs, one protocol, each side optional to the other:

- **Firmware** (CircuitPython 10.x + `adafruit_macropad`) owns everything
  latency-critical or standalone: drawing, LEDs, key handling. Unplug the
  daemon and the pad shows a quiet idle card instead of pretending.
- **Daemon** (`keymakerd`, Python ≥3.11, stdlib + pyserial) owns everything
  host-shaped: Hyprland state in and actuation out (via Hyprland's IPC
  sockets — no synthetic keystrokes), tmux window state, deck slot
  persistence, and the Omarchy palette.
- **Link**: JSON-lines over the `usb_cdc` data channel (the second CDC
  serial interface; the REPL stays free on the first). The protocol carries
  state, not commands, and the daemon re-sends a full snapshot on every
  connect — so restarts, firmware reloads, and replugs all self-heal.

Design details and the wire protocol table live in
[docs/specs/2026-08-22-switchboard-operator-design.md](docs/specs/2026-08-22-switchboard-operator-design.md).

## Requirements

- Adafruit MacroPad RP2040 flashed with CircuitPython 10.2.x
- Linux host running Omarchy (3.x or 4.x) with Hyprland
- Python ≥3.11 and `python-pyserial` on the host

## Install

```sh
git clone https://github.com/chris-biagini/keymaker.git
cd keymaker
./system/install.sh   # rsyncs firmware to CIRCUITPY, installs the user unit
```

The installer prints one manual step: a udev rule (stable
`/dev/keymaker-*` names, and it keeps ModemManager off the serial ports)
that needs root to place.

## Status

Early development. Built in the open for one specific desk — an Omarchy box
named `nexus` — with its Omarchy-4 forward compatibility taken seriously and
its portability incidental. If it works on your MacroPad too, that's a happy
accident, though issues and reports are welcome.

## License

MIT.
