# keymaker

CircuitPython firmware and a small host daemon that turn an
[Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) into a desk
companion for an [Omarchy](https://omarchy.org) / Hyprland desktop: a physical
workspace deck on one side, a finger-drumming trainer on the other, both
dressed in whatever Omarchy theme is active.

Named for the Matrix's Keymaker: a pad of keys that opens doors.

## What it does

Two apps in v1, switched from an on-device menu (long-press the knob):

### Cockpit

The 3×4 key grid is a live map of Hyprland workspaces 1–12.

- Per-key color shows state — active, occupied, urgent — in the current
  Omarchy theme's palette. Tap a key to jump to that workspace; hold it to
  move the focused window there.
- The OLED shows the focused window's app and title, the active workspace,
  a submap indicator, and a recording dot whenever anything is screencasting
  (a hardware privacy light).
- The knob is a volume knob (click to mute). It was always going to be a
  volume knob.

### Coach

A rhythm trainer for learning finger drumming, built around the pad's one
honest limitation. Stages progress from a bare metronome through backbeats,
full-kit grooves, and swing, to deliberately playing off the grid — that last
stage scored on the *consistency* of your chosen lateness, not your distance
from the click.

- Hits are timestamped and scored on the RP2040 itself (no USB or scheduler
  jitter): green within ±35 ms, amber for dragging, red for rushing.
- Stages unlock as accuracy improves; practice history persists on the host.
- Drum sounds play through the built-in speaker, and every hit is also sent
  as native USB MIDI (General MIDI drums, channel 10) — a DAW sees the pad
  exactly like any pad controller.
- The switches are on/off; velocity cannot be taught here. When your timing
  is steady enough that velocity is what's missing, the pad says so and
  retires as your trainer. The Akai awaits.

### Theme following

The daemon watches Omarchy's active theme (`colors.toml`) and pushes the
palette to the pad, which re-skins keys and screens within a couple of
seconds of a theme switch. No hooks, no templates — one file, one path, both
stable across Omarchy 3.x and 4.x.

## How it works

Two programs, one protocol, each side optional to the other:

- **Firmware** (CircuitPython 10.x + `adafruit_macropad`) owns everything
  latency-critical or standalone: coach timing and scoring, sound, MIDI,
  drawing, LEDs, app switching. Unplug the daemon and Coach still works;
  Cockpit shows a quiet idle card instead of pretending.
- **Daemon** (`keymakerd`, Python ≥3.11, stdlib + pyserial) owns everything
  host-shaped: Hyprland state in and actuation out (via Hyprland's IPC
  sockets — no synthetic keystrokes), volume via `wpctl`, the Omarchy
  palette, and practice-history persistence.
- **Link**: JSON-lines over the `usb_cdc` data channel (the second CDC
  serial interface; the REPL stays free on the first). The protocol carries
  state, not commands, and the daemon re-sends a full snapshot on every
  connect — so restarts, firmware reloads, and replugs all self-heal.

Design details, protocol table, and the Coach curriculum live in
[docs/specs/2026-08-15-keymaker-design.md](docs/specs/2026-08-15-keymaker-design.md).

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
