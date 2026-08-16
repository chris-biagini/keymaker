# Coach — design spec

Date: 2026-08-15 · Status: approved-pending-review · Branch: `coach`
Prior art: `2026-08-15-keymaker-design.md` §"Coach app" (the curriculum sketch,
authoritative where this spec is silent) · Cockpit v2 spec (conventions:
state-shaped protocol, ASCII-only OLED, Okabe-Ito colors).

The Coach is a finger-drumming rhythm trainer on the MacroPad whose end state
is retiring itself: graduating Chris to the Akai MPK mini 3 + LMMS rig.
Second app in the knob menu, beside Cockpit.

## 1. Verified facts (checked 2026-08-15, this machine/device)

| Fact | Evidence |
|---|---|
| CircuitPython 10.2.x on the pad | README §requirements; installed in v1 |
| USB-MIDI endpoint live alongside dual-CDC + HID + MSC | `lsusb -d 239a:8108 -v`: MIDIStreaming interface, MIDI_IN/OUT jacks present with current boot.py — **no boot.py change, no hard reset** |
| `adafruit_midi` already in `firmware/lib/` | ls |
| `keypad.Event.timestamp` (hardware scan time, `supervisor.ticks_ms` domain) | CP ≥ 9 API; same tick domain as `adafruit_ticks.ticks_ms` used by the framework |
| `audiopwmio` + `audiomixer` + `audiocore` in RP2040 CP 10 builds | CP support matrix; **bench-verify at Task 1** |
| Speaker: `board.SPEAKER` (PWM) + `board.SPEAKER_ENABLE` | MacroPad hardware |
| Deploy = `system/install.sh` rsync to CIRCUITPY | v1 |
| App framework: `App` subclass, knob-menu registration in `code.py` | `firmware/pad/framework.py` |

## 2. Decisions (from design Q&A)

- **MPK mirror layout**: kick=key 9, snare=key 10, hat=key 11 (bottom row,
  left→right) — matches MPK mini Bank A order so muscle memory transfers.
- **Synthesized drums**: `tools/make_drums.py` (stdlib-only) generates the
  WAVs; outputs committed to `firmware/sounds/`. No external samples.
- **Session = fixed 16 bars**: 1 count-in bar + 8 two-bar loops, auto-stop.
- **One plan** for the full curriculum, stages 0–5 + graduation.
- **Beat flash, not cursor**: top row is 3 wide vs 4 beats/bar, so the top
  row flashes as a unit each beat (accent on the 1) — a visual click track.
  Approved as a delta from the earlier cursor sketch.

## 3. Units

| Unit | Responsibility |
|---|---|
| `shared/km_coach.py` | Pure, no hardware imports: stage table, pattern/grid generation (incl. swing), hit matching, classification, session scoring, stage-5 variance scoring, `summarize(history)` (unlocks, bests, recents), graduation predicate. Both firmware and daemon import it. |
| `firmware/apps/coach.py` | The app: state machine, metronome scheduler, LED/OLED rendering, MIDI note emission, RAM session queue. All judgment delegated to `km_coach`. |
| `firmware/pad/audio.py` | `PWMAudioOut(board.SPEAKER)` + 4-voice `audiomixer.Mixer`; `play(name)` non-blocking; `enable()/disable()` wrapping `SPEAKER_ENABLE`. Init failure → object with no-op `play` (LEDs/MIDI still work). |
| `tools/make_drums.py` | Generates `firmware/sounds/{click_hi,click_lo,kick,snare,hat}.wav`, 22.05 kHz 16-bit mono. Recipes in §8. |
| `daemon/keymakerd/coach_store.py` | Loads/appends `~/.local/state/keymaker/coach.json` (atomic tmp+rename), stamps received sessions with wall-clock `ts`, produces the `coach` state message via `km_coach.summarize`. |
| Framework tweak | `framework.py` passes `event.timestamp` (not loop `now`) to `on_key_event`; adds `App.on_hide()` (no-op default) called on the outgoing app at menu switch. Coach's `on_hide` aborts any session and disables the speaker. |

## 4. Curriculum as data

Two-bar loop = 32 sixteenth slots (0–31); beats at slots 0,4,8,12 per bar.
Stage table in `km_coach.STAGES` (index = stage number):

| # | Name | Pattern (instrument: slots) | Notes |
|---|---|---|---|
| 0 | metronome | — (empty) | free play, nothing scored |
| 1 | on the one | kick: 0, 16 | the One of each bar |
| 2 | backbeat | kick: 0,8,16,24 · snare: 4,12,20,28 | |
| 3 | the pocket | stage 2 + hat: every even slot (0,2,…,30) | eighth hats |
| 4 | swing | stage 3; off-beat hats (slots ≡ 2 mod 4) shifted | dial = swing % live, §6 |
| 5 | off the grid | kick: 0,8,16,24 · snare: 4,12,20,28 — **no hats** | snare variance-scored, §5 |

Stage 5 drops the hats deliberately: the Dilla skill is the tension between a
straight kick and a drunk snare; hats would blur the measurement.

Swing math (stage 4): within each quarter note of duration `Q` ms, the
off-beat hat moves from `Q/2` (straight) to `(s/100)·Q`, `s ∈ [50, 67]`
(67 ≈ full triplet). Grid regenerates at loop boundaries only.

## 5. Scoring (all in `km_coach`, pure)

- **Windows**: |offset| ≤ 35 ms → `green`; offset < −35 (early) → `red`
  (rushing); offset > +35 (late) → `amber` (dragging); |offset| > 120 ms →
  no match.
- **Matching**: hits processed in time order; each hit greedily matches the
  nearest unmatched expected slot *of its instrument* within ±120 ms. A hit
  with no match — including a second hit on an already-matched slot, or any
  hit on an instrument absent from the stage pattern — is a `stray`.
  Expected slots left unmatched at window expiry are `miss`es. Deterministic
  pure function of `(expected_times, hits)`.
- **Session accuracy** = greens / (expected_total + strays). Empty
  denominator → 0.
- **Stage 5**: kick grid-scored as usual. Snare offsets are measured against
  nominal slots; `variance_score = max(0, 1 − pstdev(offsets)/40)` (ms), and
  the session counts **only if mean offset ≥ +10 ms** (you must actually be
  late, else score = 0 with OLED hint "drag it"). Session score =
  `min(kick_accuracy, variance_score)`.
- **Unlock rule**: stage n+1 unlocks when stage n has ≥ 3 recorded sessions
  and the mean score of the 3 most recent ≥ 0.85. Any BPM counts.
  Stages 0 and 1 are always available.
- **Graduation**: stage 5 satisfies the same rule → graduation card. State
  is derived, never stored: `summarize(history)` recomputes everything.

## 6. Session flow and controls

States: `idle → countin → playing → results → idle`.

- **idle**: keys 0–5 select a stage (tap an unlocked stage → `countin`;
  locked stage → brief red flash). Bottom-row drums always live (sound +
  MIDI, unscored). Dial = BPM, 60–140, 5/detent, default 95. Knob-click =
  nothing in idle.
- **countin**: one bar of clicks (accent on 1). Drum hits sound but are
  neither scored nor strays.
- **playing**: 8 two-bar loops (16 bars). Epoch = `ticks_ms` at countin
  start; every event time derives from the epoch (`epoch + slot·sixteenth_ms`)
  via `ticks_diff` (wrap-safe) — no chained intervals, no drift. Click on
  every beat; expected-hit windows close 120 ms after their slot. In stage 4
  the dial adjusts swing % live (applied next loop); elsewhere the dial is
  inert during play. Knob-click = abort (no record).
- **results**: score, breakdown, unlock/graduation notice. Any key or 6 s
  timeout → idle. Session record → RAM queue → link (§7).

BPM changes apply in idle only (next session); mid-session the grid is fixed.

## 7. Protocol and persistence (additive, state-shaped)

| Dir | Message | When |
|---|---|---|
| p→h | `{"t":"coach","session":{"stage":n,"bpm":n,"swing":n\|null,"greens":n,"ambers":n,"reds":n,"misses":n,"strays":n,"score":f,"duration_ms":n}}` | session end; queued in RAM if link down, flushed on link-up |
| h→p | `{"t":"coach","unlocked":n,"stages":{"1":{"best":f,"recent":[f,…≤3]},…},"practice_ms":n}` | in the connect snapshot, and after each accepted session (state-shaped ack: the pad learns its session landed by receiving refreshed state) |

- `coach.json`: `{"version":1,"history":[{…session fields…,"ts":"iso8601"}]}`.
  Daemon stamps `ts` at receipt (the pad has no clock). Everything else —
  unlocks, bests, recents — is derived on read via `km_coach.summarize`.
  Atomic write (tmp + `os.replace`). Malformed/missing file → empty history,
  logged, never fatal.
- Standalone (no daemon): fully playable. Cold start unlocked = stage 1;
  progress earned live this power-cycle applies immediately; on link-up the
  host state merges by `max(unlocked_local, unlocked_host)` after queued
  sessions flush.
- Unknown message types remain ignored on both sides (existing contract).

## 8. Sound and MIDI

- Mixer: 4 voices — click, kick, snare, hat; overlapping one-shots never cut
  each other. Click voice plays `click_hi` (beat 1) or `click_lo` (2/3/4).
  Speaker level fixed; system volume remains Cockpit's job.
- WAV recipes (`tools/make_drums.py`, stdlib `wave`+`math`+`random`, peak
  −3 dBFS, all durations/frequencies bench-tunable): kick = sine sweep
  120→45 Hz, 180 ms, exp decay; snare = 185 Hz tone (80 ms) + white noise
  (120 ms); hat = first-difference-filtered noise, 45 ms; click_hi/lo =
  1.5/1.0 kHz, 10 ms.
- MIDI: every kick/snare/hat hit (all states incl. idle noodling) sends
  `NoteOn` + immediate `NoteOff`, channel 10 (index 9), GM: kick 36,
  snare 38, closed hat 42, fixed velocity 100. Own
  `adafruit_midi.MIDI(usb_midi.ports[1], out_channel=9)` constructed in
  `coach.py`, import-guarded: absent `usb_midi` → speaker-only, no crash.
  The click never goes to MIDI.

## 9. Panel (LEDs + OLED, ASCII only)

Colors are fixed Okabe-Ito values (CVD-safe, consistent with `INDEX_BINS`):
on-time `0x009E73` (bluish green), dragging `0xE69F00` (orange), rushing and
stray/locked `0xD55E00` (vermillion), beat accent `0xF0E442` (yellow).

| State | Keys 0–2 (row 0) | Keys 3–5 | Keys 6–8 | Keys 9–11 (drums) |
|---|---|---|---|---|
| idle | stages 0–2 | stages 3–5 | dark | dim gray `0x141414` |
| countin/playing | beat flash: accent yellow on 1, dim white pulse on 2/3/4, ~120 ms | dark | dark | rest dim gray; hit → 150 ms flash in verdict color; miss → vermillion flash on the expected drum at window expiry |
| results | unlock fanfare sweep if earned, else dark | dark | dark | dim gray |

Stage-select colors = `km_palette.INDEX_BINS[stage]`, full brightness when
unlocked, scaled ~0.12 when locked.

OLED (inverted header per v2 convention, lowercase ASCII):

- idle — header `coach`; line1 `stage <n> <name>` (last selected); line2
  `bpm 95  best 92%`; footer `tap a stage  knob bpm`
- playing — header `<n> <name>`; line1 `bpm 95  bar 7/16` (stage 4:
  `swing 62%  bar 7/16`); line2 `acc 91%` live; footer `click = stop`
- results — header `results`; line1 `<name>  score 88%`; line2
  `g14 a2 r1 m1 s0`; footer `unlocked: <next name>` / `need 85% x3` /
  stage-5 gate fail: `drag it`
- graduation — header `graduated`; line1 `you have consistency`; line2
  `you need velocity`; footer `the akai awaits`

## 10. Degradation

| Absent | Behavior |
|---|---|
| Daemon / link | Fully playable; RAM queue; cold start = stage 1 unlocked |
| `usb_midi` | Speaker-only; import-guarded |
| Audio init fails | Silent trainer: LEDs/OLED/MIDI/scoring all still work |
| `coach.json` malformed | Empty history, logged, non-fatal |
| Mid-session app switch / menu | `on_hide` aborts session (no record), speaker disabled |

## 11. Testing

- pytest on `km_coach`: pattern generation per stage (incl. swing at s=50
  straight and s=67 triplet), window boundaries (34/35/36, 119/120/121 ms),
  greedy matching (duplicate hit → stray; off-pattern instrument → stray),
  accuracy incl. empty denominator, variance scorer (steady-late → 1.0;
  known stddev; mean-lateness gate), unlock rule (2 sessions insufficient;
  mean vs each-of-3), graduation predicate, `summarize` round-trip.
- pytest on `coach_store`: append + atomic write, ts stamping, malformed
  file recovery, state-message shape.
- Firmware logic that must be testable stays in `km_coach`; `coach.py` is
  wiring. Existing 64 tests keep passing.
- Bench checklist (hardware-in-loop, with Chris): audiomixer import + WAV
  playback; timing feel at 60/95/140 BPM; MIDI notes visible in LMMS
  per-instrument tracks; scoring windows sanity; count-in feel; graduation
  path (with a temporarily loosened unlock rule for the demo).

## 12. Tunables (pinned now, adjusted only at the bench)

Green window 35 ms · miss window 120 ms · variance divisor 40 ms · stage-5
mean-lateness gate +10 ms · unlock mean 0.85 over last 3 · BPM default 95,
step 5 · flash 150 ms · beat flash 120 ms · velocity 100 · results timeout
6 s · WAV recipes (§8).

## 13. Out of scope

Velocity sensitivity (hardware can't), open hat / toms / fills, pattern
editor, host-side stats UI, sounds via the host, Akai-side configuration
(that's the beat-making rig, already documented in oracle setup.md).
