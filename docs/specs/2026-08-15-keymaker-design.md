# Keymaker — Design Spec

*2026-08-15. Status: approved sections, pending final review.*

Keymaker turns an Adafruit MacroPad RP2040 into an Omarchy-native desk companion
for `nexus`: a **Hyprland cockpit** (workspace deck + volume knob + privacy
light) and a **rhythm coach** (a finger-drumming trainer that graduates its
student to an Akai MPK mini). The pad follows the active Omarchy theme. Named
for the Matrix's quiet craftsman: a pad of keys that opens doors.

## Hardware and baseline

| Item | Detail |
|---|---|
| Device | Adafruit MacroPad RP2040 (product 5128), USB ID `239a:8108` |
| I/O | 12 MX keys w/ per-key NeoPixel, rotary encoder w/ push, 128×64 SH1106 OLED, speaker, USB-C |
| Firmware today | CircuitPython **7.0.0-alpha.5** (2021, factory state) — must be replaced |
| Firmware target | CircuitPython **10.2.1** + `adafruit_macropad` 2.4.x from the 10.x bundle |
| Host | `nexus`, Arch/Omarchy 3.8.4 (Hyprland/Wayland); must survive the Omarchy 4 "Quattro" upgrade |

Flash procedure (physical, Chris's hands): back up `CIRCUITPY`, enter bootloader
(**hold the encoder knob, tap the reset button on the back**), flash
`flash_nuke.uf2` to erase (mandatory across the 5-year alpha jump — all 7.x-era
`.mpy` files are format-incompatible), then flash the 10.2.1 UF2, then copy
fresh 10.x bundle libraries.

## Architecture: smart device, peer daemon

Two programs, one protocol, **each side optional to the other**:

- **Firmware** (CircuitPython, on the pad) owns everything latency-critical or
  standalone: coach timing/scoring (RP2040 timestamps, no USB/scheduler
  jitter), metronome + drum WAVs through the speaker, USB-MIDI note emission,
  all screen drawing and LED animation, app switching.
- **Daemon** (`keymakerd`, Python on nexus) owns everything host-shaped:
  Hyprland state in/actuation out, Omarchy theme palette, volume, practice-
  history persistence.
- **Link**: JSON-lines over the `usb_cdc` **data** channel (second CDC
  interface; the console channel stays free for REPL debugging). The protocol
  is *state-shaped*, not command-shaped: the daemon re-sends a full snapshot on
  every (re)connect, so daemon restarts, firmware auto-reloads, and USB replugs
  all self-heal with no handshake state.

Degradation: without the daemon, Coach is fully functional (RAM-only session
stats); Cockpit shows a quiet "no link" idle card with keys dark — no fake
state.

## Firmware

### Framework

`code.py` runs one loop polling four sources — key events, encoder delta,
encoder switch, serial inbox — and dispatches to the active app. An app
implements: `on_key(event)`, `on_dial(delta)`, `on_click()`, `on_msg(msg)`,
`tick(now)` / `draw()`. The framework owns: theme palette (daemon-pushed;
baked-in default when unlinked), LED brightness, app switching, the link.

**Global gesture:** long-press the knob (≥600 ms) → app menu on the OLED;
rotate to browse, click to enter. Short-click is app-local. v1 ships Cockpit
and Coach; future apps (sequencer, Simon, ops board) register in the menu.

Logic that can be pure Python **must** be pure Python (no CircuitPython
imports): the coach scorer, protocol codec, palette mapping. These modules run
unmodified under host CPython for pytest/TDD.

### Cockpit app

- **Keys 1–12 = Hyprland workspaces 1–12**, grid read like a page (key 1
  top-left). Tap → `dispatch workspace N`. Hold (≥400 ms) → `dispatch
  movetoworkspacesilent N` (move focused window there).
- **Key colors** from the live theme: active = accent full; occupied = accent
  heavily dimmed; empty = off; urgent = theme red, pulsing.
- **OLED**: focused window app name + title (marquee when long), active
  workspace number, submap glyph when a submap is active, and a **recording
  dot** whenever Hyprland reports a `screencast` — a hardware privacy light.
- **Knob**: volume via `wpctl` (host-side), detent = step; click = mute
  toggle. Mute state mirrored on the OLED.

### Coach app ("the Dilla trainer")

A rhythm trainer whose end state is graduating to the Akai MPK mini 3 + LMMS
rig already configured on nexus (see oracle `docs/setup.md` § beat-making).

**Curriculum** (4/4, two-bar loops; knob = BPM 60–140):

| Stage | Name | Task | Skill |
|---|---|---|---|
| 0 | Metronome | free play over the click | hands on pads |
| 1 | On the One | kick on downbeats | basic pocket |
| 2 | Backbeat | kick 1·3, snare 2·4 | two-finger independence |
| 3 | The Pocket | + eighth-note hats | full-kit groove |
| 4 | Swing | knob sets swing %, hats follow the swung grid | feel vs grid |
| 5 | Off the Grid | drag the snare deliberately late; scored on **consistency of the chosen offset** (low variance), not grid distance | steady drunk — the Dilla skill |

**Scoring** (on-device timestamps): green = within ±35 ms; **early** beyond
that = red (rushing); **late** = amber (dragging); >120 ms = miss. Session
accuracy = fraction green. Stage 5 replaces grid distance with offset-variance
scoring: per session, score = max(0, 1 − stddev(offsets)/40 ms), computed over
the player's snare offsets relative to their own session-mean offset (the
target being *steadiness*, not any particular lateness). Stage 4 swing range:
50–67% (straight to full triplet). Exact windows and constants are tunable,
finalized during hardware-in-loop testing.

**Progression:** earned unlocks — next stage opens at ≥85% accuracy across 3
sessions. Completing Stage 5's bar triggers the graduation ceremony on the
OLED: *"You have consistency. You need velocity. The Akai awaits."* (Velocity
is the one thing these on/off switches cannot teach — the hardware retires
itself as trainer.)

**Sound, dual path, always both:** kick/snare/hat one-shot WAVs (CC0, mono
22.05 kHz 16-bit) through the built-in speaker, **and** native USB-MIDI notes
(GM drum map, channel 10: 36 kick / 38 snare / 42 closed hat) — so LMMS
receives the pad exactly like the MPK (per-instrument MIDI-in, same wiring
documented in setup.md).

**Persistence:** host-side at `~/.local/state/keymaker/coach.json` (schema:
per-stage best accuracy + session log, unlocked stage, total practice time),
synced over the link; restored on connect. (CircuitPython's own filesystem is
read-only to code while USB mass storage is mounted, so host-side storage is
both necessary and where backups belong.)

## Protocol (JSON-lines, `usb_cdc` data channel)

| Dir | `t` | Payload | Purpose |
|---|---|---|---|
| h→p | `hello` | host, proto ver | link up; full snapshot follows |
| h→p | `ws` | active, occupied[], urgent[] | workspace state |
| h→p | `win` | class, title (pre-truncated) | focused window |
| h→p | `flags` | submap, screencast, muted | status glyphs |
| h→p | `palette` | theme name + named colors (accent, bg, fg, red, …) | theme following |
| h→p | `coach` | stored history | progression restore |
| p→h | `hello` | fw version, active app | announce; requests snapshot |
| p→h | `key` | n, tap\|hold | Cockpit actuation |
| p→h | `dial` / `click` | ±n / — | volume / mute |
| p→h | `coach` | stage, accuracy, session stats | persist |
| both | `ping`/`pong` | — | 5 s heartbeat; pad marks link down at 15 s |

Firmware derives dim/pulse variants from named palette colors by RGB scaling.
MIDI is a separate native USB endpoint and never rides this protocol.

## Daemon (`keymakerd`)

Python ≥3.11, dependencies: stdlib + `pyserial` (pacman `python-pyserial`).
Runs from the repo checkout; no packaging in v1. Four asyncio jobs:

1. **Serial link** — opens `/dev/keymaker-data` (udev symlink), JSON-lines
   codec, reconnect with backoff on EOF/exception; full snapshot on connect.
2. **Hyprland** — resolves `$XDG_RUNTIME_DIR/hypr/<newest>/`; seeds via
   `-j`-style requests on `.socket.sock`; streams `.socket2.sock` events
   (workspace, activewindow, urgent, submap, screencast); re-derives the
   instance on reconnect (survives Hyprland restarts; no env import needed).
3. **Theme watcher** — resolves the current-theme dir dynamically:
   `~/.local/state/omarchy/current/theme` (4.0) **then**
   `~/.config/omarchy/current/theme` (3.x); parses `colors.toml` with stdlib
   `tomllib`; polls symlink target + mtime every 2 s; pushes `palette` on
   change. No Omarchy hook, no template — the only contracts used are
   `colors.toml` key names and the theme path (both stable across 3→4).
4. **Actuation + persistence** — `key` → `.socket.sock` dispatch; `dial`/
   `click` → `wpctl`; `coach` → `~/.local/state/keymaker/coach.json`.

## System plumbing (Quattro-proof by construction)

- **udev rule** (the one `sudo` step, Chris runs it): match VID `239a` PID
  `8108`; `SYMLINK+="keymaker-repl"` / `"keymaker-data"` per CDC interface;
  `ENV{ID_MM_DEVICE_IGNORE}="1"` (keep ModemManager off the ports).
- **systemd user unit** `keymaker.service`: `WantedBy=graphical-session.target`
  (provided by Omarchy's uwsm session on 3.x and 4.0), `Restart=always`,
  `RestartSec=2`. Reconnect logic lives in the daemon, not in device units
  (systemd #16510 makes user device-units flaky).
- **Deliberately not used** (Quattro forward-compat): no `exec-once`
  autostart (no-op under Quattro's Lua config), nothing from the retired 3.x
  stack (waybar/mako/playerctl/swayosd/hypridle hooks), no files in tool-owned
  config dirs, no symlinks into `~/.config/omarchy/plugins/`, no
  `~/.local/share/omarchy` paths.

## Repo layout

```
keymaker/
├── firmware/            # rsync'd to CIRCUITPY root
│   ├── boot.py          # usb_cdc.enable(console=True, data=True)
│   ├── code.py          # loop + app switcher
│   ├── keymaker/        # framework: link, palette, ui; pure-python modules
│   ├── apps/            # cockpit.py, coach.py (+ coach_scoring.py, pure)
│   └── sounds/          # kick/snare/hat WAVs
├── daemon/keymakerd/    # asyncio daemon package
├── system/              # 99-keymaker.rules, keymaker.service, install.sh
├── tests/               # pytest: scorer, codec, hyprland parser, palette
└── docs/specs/          # this document
```

`install.sh` handles: firmware rsync to `CIRCUITPY`, systemd unit install +
enable, state-dir creation; prints the udev step for Chris to sudo.

## Testing

- **pytest on nexus** for every pure module: scorer windows + Stage-5 variance
  scoring, protocol codec (both ends share it), Hyprland event parser (fed
  captured real `socket2` transcripts), palette loader (fed real installed
  Omarchy theme files). TDD per superpowers for all of these.
- **Hardware-in-loop**: Oracle drives the REPL on `/dev/keymaker-repl` for
  smoke tests (imports clean, app switch, palette apply); UX feel (LED
  timing, marquee, menu) iterated live with Chris.
- **Integration**: daemon against a pty-backed fake serial port; end-to-end
  workspace-jump verified against the live Hyprland session.

## Non-goals (v1)

- No velocity sensitivity (hardware cannot; it's the graduation reason).
- No additional apps (sequencer, Simon, ops board) — the framework's menu
  makes them cheap later.
- No Quattro bar plugin / omarchyplugins.com widget — possible follow-up.
- No support for hosts other than nexus/Omarchy (portability is incidental).

## Milestones

1. Firmware flash to 10.2.1 + libs (Chris's hands, Oracle staging).
2. Framework + Cockpit + daemon (Hyprland/theme/volume) end to end.
3. Coach stages 0–3 with scoring + persistence; MIDI into LMMS verified.
4. Coach stages 4–5, graduation, polish (sounds, animations, idle cards).
5. README + docs polish; keymaker section in oracle `docs/setup.md`.

The public GitHub repo (`chris-biagini/keymaker`) is created and pushed
immediately after spec approval — the project is built in the open from
milestone 1, not published at the end.
