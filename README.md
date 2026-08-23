# keymaker

CircuitPython firmware and a small host daemon that turn an
[Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) into a desk
companion for an [Omarchy](https://omarchy.org) / Hyprland desktop: a physical
switchboard of open terminal windows, dressed in whatever Omarchy theme is
active.

Named for the Matrix's Keymaker: a pad of keys that opens doors.

## What it does

A single app: **Cockpit**, the split deck.

- **Top six keys = Hyprland workspaces 1–6**, each lit in its workspace's
  colorhash color (same Petroff-10 palette as the tmux status bar): active
  full-bright, occupied dimmed, urgent pulsing, empty dark. Tap to switch
  workspace; hold to move the focused window there silently.
- **Bottom six keys = windows on the active workspace**: the tmux windows of
  its `ws-*` session first (each in the colorhash color of its window NAME, the
  same cell the tmux status bar paints), then sessionless terminals (which have
  no stable name, so they take the cell of the key they land on). Focused
  full-bright, others dimmed, bell pulsing. Tap to jump to that window.
- The OLED is a weather display: sparse digital rain while all is calm, and
  when terminal bells ring elsewhere, a wall of workspace numerals sized by
  recency — the newest bell largest. A workspace switch flashes that
  workspace's numeral, big and centred, for a moment; REC and submap badges
  overlay everything while the screen is being captured or a submap is
  active.
- Urgency runs BEL → tmux → foot → Hyprland → pad, entirely in-band, so it
  works the same over mosh as it does locally.
- The knob is currently unassigned.

### Theme following

The daemon watches Omarchy's active theme (`colors.toml`) and pushes the
palette to the pad, which re-skins keys and screens within a couple of
seconds of a theme switch. No hooks, no templates — one file, one path, both
stable across Omarchy 3.x and 4.x.

## How it works

Two programs, one protocol, each side optional to the other:

- **Firmware** (CircuitPython 10.x + `adafruit_macropad`) owns everything
  latency-critical or standalone: drawing, LEDs, key handling. Unplug the
  daemon and the pad keeps its rain running with a small `no link` tag
  instead of pretending.
- **Daemon** (`keymakerd`, Python ≥3.11, stdlib + pyserial) owns everything
  host-shaped: Hyprland state in and actuation out (via Hyprland's IPC
  sockets — no synthetic keystrokes), tmux window state, and the Omarchy
  palette.
- **Link**: JSON-lines over the `usb_cdc` data channel (the second CDC
  serial interface; the REPL stays free on the first). The protocol carries
  state, not commands, and the daemon re-sends a full snapshot on every
  connect — so restarts, firmware reloads, and replugs all self-heal.

The split deck's original design lives in
[docs/specs/2026-08-15-cockpit-v2-design.md](docs/specs/2026-08-15-cockpit-v2-design.md)
(the 2026-08-22 switchboard-operator spec describes a sticky-slot design that
was tried and reverted the same day); the OLED's weather display design lives
in [docs/specs/2026-08-23-oled-weather-design.md](docs/specs/2026-08-23-oled-weather-design.md).

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
