# Pad Timing and Performance

What the MacroPad RP2040 can and cannot do per main-loop pass, extracted from the
Coach drum trainer before its deletion. Coach was the only subsystem in this
project with hard timing requirements; everything below is what it proved, plus
the two rendering bugs that proved the same lesson from the other direction.

Sources are cited as `file:line` or commit sha throughout. Where a claim is
inferred rather than measured, it says so.

## 1. There is no fixed tick rate — and no measurement of it

`firmware/pad/framework.py` `run()` is a bare `while True:` with no `time.sleep`.
Loop frequency is whatever CircuitPython achieves while polling the encoder
switch, encoder position, key events, and link messages, then calling
`app.tick(now)`. Unthrottled and unmeasured.

**No commit, spec, or test in this repo asserts a loop rate, tick period, or
per-tick millisecond cost.** That number does not exist. Any future work needing
one must measure it fresh.

The practical consequence is the opposite of a budget: there is no tick allowance
to spend down. What matters is that per-tick work stays cheap and idempotent —
see §5.

## 2. Timestamp accuracy is real, and does not come from the loop

`macropad.keys.events.get()` yields `keypad.Event` objects whose `.timestamp` is
the **hardware key-scan time**, in the same `adafruit_ticks.ticks_ms()` domain as
everything else. `framework.py` passes `event.timestamp` — not the loop's `now` —
into `App.on_key_event(n, pressed, now)`. Wired in commit `0fd4c69`.

This is why Coach's tolerances were meaningful despite an unmeasured loop: hits
were timestamped by hardware, not by when the loop noticed them. A design that
timestamped at `tick(now)` would inherit whatever loop jitter exists — which is
untested, because the architecture deliberately avoided needing to know.

No software debounce sits between hardware scan and the app. `shared/km_keys.py`
`KeyTracker` only classifies tap-vs-hold from already-debounced press/release
timestamps (`hold_ms=400` default).

## 3. Schedule from a fixed epoch, never from chained deltas

Recorded as a hard constraint in the Coach spec and honored throughout
`firmware/apps/coach.py`: an epoch is captured once at session start, and every
event time is computed as `epoch + offset` and compared with `ticks_diff`. No
running "next event" clock incremented relative to the previous event.

`ticks_diff` / `ticks_add` from `adafruit_ticks` handle `ticks_ms()` wraparound.
**Raw tick values are never compared directly.** This applies to any timed
gesture, including a hold-to-confirm countdown.

The one accumulator in Coach (`_next_click += self.quarter`, free-play
metronome) re-anchors to `ticks_diff(now, self.epoch)` each pass, so a slow tick
causes catch-up rather than drift.

**One logical instant, one clock read.** Commit `7640022`: `Screen.marquee()`
seeded its epoch from its own `ticks_ms()` while `Screen.tick(now)` compared
against the `now` the caller had read earlier in the same loop pass. Both reads
name the same instant, but the later one is larger, so `ticks_diff(now, epoch)`
came back *negative* whenever the frame gate fired on the pass that started the
wipe — and `marquee_x` returns `None` for a negative elapsed, which is
indistinguishable from "finished". The wipe silently cancelled before drawing a
frame. Pass one `now` down through everything that participates in a single
instant; where an API cannot, clamp at the comparison. Two reads of the same
clock can straddle a comparison, and the interval between them is not
guaranteed to have a sign.

That bug survived a purpose-built stub-`displayio` harness, which is the wider
caution: `firmware/pad/ui.py` and `firmware/apps/cockpit.py` cannot be imported
on the host, so a hand-built harness is their entire safety net — and it only
ever covers the interleavings whoever wrote it thought to write. Every test in
that harness advanced the clock before triggering the marquee, so the
zero-elapsed case was never exercised. Treat a passing harness as evidence about
the paths it enumerates, not about the file. Logic that can be moved into
`shared/` and tested for real should be.

## 4. A fixed-width window abutting a state change is governed by the window

`HORIZON_MS = 200` — the next loop's expected hits were emitted into the scorer
200ms before they were due, so a legitimately early hit had a slot to match
against.

Commit `e315158` fixed the corollary: the final 120ms of the nominally-unscored
count-in had to be scored anyway, because that window already belonged to bar 1's
matching window. The general rule: when a fixed-width matching or lookahead
window abuts a state transition, the *window's* edge governs what counts, not the
*state's* edge.

## 5. Drawing is not free to call every tick

This is the load-bearing finding for any future app on this framework.

`pixels.auto_write` is left `True` by design (`framework.py`, comment at the
ledtest branch). **Every `pixels[i] = ...` assignment drives the entire NeoPixel
strip immediately.** An unconditional 12-pixel repaint is twelve full-strip
writes per pass, not one.

Coach guarded against this from the start: `_set_px` only assigns when the color
differs from a cached value. Two later bugs in Cockpit proved what happens
without the guard:

| Commit | Symptom | Cause | Fix |
|---|---|---|---|
| `cce8f41` | LEDs strobing | Cleared all 12 pixels then repainted, every tick — with `auto_write` on, that drove the strip through black and back each pass | Compute the frame, diff against the last written frame, write only changed pixels. A static deck costs **zero** writes per tick |
| `0233a64` | OLED flickering | `map_bmp.fill(0)` then full redraw, every tick — dirtying a `displayio.Bitmap` at loop frequency forces continuous panel refresh | Gate the redraw on a content signature |
| `489c99a` | OLED still blinking periodically | The blink phase was in that signature unconditionally, so it flipped twice a second forever even with nothing ringing; and clear-then-repaint leaves a blank frame `displayio` can scan out | Blink participates in the signature only when there are bells; paint the bitmap by difference, never clear it |

The pattern is uniform across all three: **cache what was last actually written,
and touch hardware only when it changes.** Note that none of these were found by
profiling — each surfaced as a visible artifact on hardware. There is no CPU-time
evidence here at all, only symptom-and-fix.

## 6. Audio subsystem

Retained for reference; the audio path is deleted along with Coach and this is
the only surviving record of it.

| Fact | Value |
|---|---|
| Output path | `audiopwmio.PWMAudioOut(board.SPEAKER)` → `audiomixer.Mixer` → `audiocore.WaveFile` |
| Mixer voices | 4, so overlapping one-shots never cut each other |
| Sample format | 22050 Hz, mono, 16-bit signed PCM |
| WAV peak | −3 dBFS |
| `play()` | Non-blocking — hands a `WaveFile` to a voice and returns |
| Init failure | Any exception sets `_mixer = None` and `play()` becomes a no-op; the app degrades to silent rather than dying |

**Speaker enable must be gated (commit `57e1eb9`).** The PWM output runs
continuously once started; with the amp enabled, that idle carrier is audible as
hiss even with nothing playing. `SPEAKER_ENABLE` is therefore asserted at `play()`
and released after `IDLE_MS = 1500` of silence. The 1500 figure is derived, not
arbitrary: it must exceed the widest in-tempo event gap (1000ms at 60 BPM) so a
session never gates mid-groove. Any future idle-gate timeout must be derived the
same way.

**The speaker has no low end (commit `57e1eb9`).** A physically-correct 120→45 Hz
kick sweep was nearly inaudible on the ~20mm driver. The fix was a *higher*
190→50 Hz sweep plus a 3ms noise transient. Synthesis parameters that are
correct on paper can be inaudible on this hardware; any audio feature needs a
bench listening pass.

Runtime synthesis was never attempted. All samples were pre-baked WAVs generated
by a deterministic seeded script and committed, with a test enforcing that the
committed bytes match the generator.

## 7. Deploying can race CircuitPython's autoreload

From commit `57e1eb9`, and still live for every future firmware change: an rsync
landing mid-autoreload caused FAT rename errors, the kernel remounting CIRCUITPY
read-only, and a 30-second USB reset loop — recoverable only by killing writers,
replugging, and remounting.

`system/deploy-firmware.sh` guards this by pausing autoreload over the REPL
serial port before the rsync, then firing one clean reload afterward. It also
hardens against a stale read-only mount and excludes `__pycache__`. **This guard
must stay.**

Since 2026-09-05 `firmware/boot.py` hides the drive outright
(`storage.disable_usb_drive()`), which removes the window rather than narrowing
it: with no host mount in normal operation there is nothing to race. The guard
still runs, because the deploy re-exposes the drive for exactly one boot.

Recovering from a FAT that has already gone read-only needs the pad, not the
host. Unmounting and remounting does not clear it — the medium is still
presented write-protected, and CircuitPython cannot write it either. Only a hard
reset re-presents it. The deploy script's read-only branch does that: reset back
to hidden, then expose fresh, and it gives up after one retry.

## 7a. A serial write is not a reset

Two host-side operations look synchronous and are not.

CircuitPython's USB CDC discards input written before it registers that the host
has opened the port. A Ctrl-C sent immediately after `open()` is lost. This was
invisible until the drive got hidden, because the deploy then depends on serial
for control rather than convenience: `hide` failed on the first attempt every
single time and succeeded on the retry. `system/pad-repl.py` settles for 500 ms
after opening before its first write.

And `microcontroller.reset()` returning does not mean the pad reset. Bytes
leaving the host prove nothing. Both reset paths now wait for the serial device
node to disappear, which only happens on a real USB re-enumeration, and
`deploy-firmware.sh` separately confirms the drive is gone before it reports
success.

## 8. Deliberate non-attempts

- **No velocity sensitivity.** The key switches are on/off. A hardware limit, not
  a performance one.
- **No runtime audio synthesis.** Playback of fixed samples only.
- **No loop-rate instrumentation.** See §1 — the number does not exist.
