# OLED Weather — Rain, Bell Wall, Marquee

**Date:** 2026-08-23
**Status:** Approved design, pre-plan
**Builds on:** Cockpit v2 split deck (docs/specs/2026-08-15-cockpit-v2-design.md).
Replaces the placeholder text OLED (`firmware/pad/ui.py`) whose design was
explicitly left open.

## 1. Overview

The pad sits at arm's length, tilted; 6px text on the 128×64 OLED is
unreadable from where Chris actually is. The keys already carry the detailed
state (that was the split deck's thesis), so the OLED stops being a page and
becomes a poster: big shapes, motion, and no body text at all.

The screen is always in exactly one of three **weather states**, with two
overlays that ride on top of any of them:

| State | Trigger | What shows |
|---|---|---|
| **Calm** | no urgent workspaces | sparse digital rain (Matrix-style falling glyphs) |
| **Ringing** | ≥1 urgent workspace | the **bell wall**: the most recently belled workspace as a ~48px numeral, other ringing workspaces as ~24px numerals beside it, ordered by recency |
| **No-link** | daemon disconnected | rain, plus a small inverted `no link` corner tag |

Overlays:

- **Badge layer** (topmost, persistent): inverted `REC` block top-right while
  Hyprland reports a screencast; submap badge beside it, dropped first on
  collision — same priority rule as today (`shared/km_text.py`: an unnoticed
  screencast is the costliest badge to miss).
- **Marquee layer** (transient): on workspace switch, a large numeral wipes
  across the screen for ~600ms, then reveals whichever base state is
  underneath.

Dropped entirely: the identity header (workspace number + name) and the
focused-window title. Decision Chris, 2026-08-23 — "what's ringing right now"
is the OLED's one job; where-am-I and what's-focused belong to the keys.

## 2. Facts this design stands on

| Fact | Evidence |
|---|---|
| OLED is 128×64, 1-bit monochrome (SH1106) | Adafruit MacroPad RP2040 hardware; `displayio` root group in `pad/ui.py` |
| Urgent workspaces already reach the pad | `ws` message carries `urgent: [n, ...]` (`firmware/apps/cockpit.py` `_ws_state`) |
| Screencast + submap flags already reach the pad | `flags` message (`cockpit.py` `on_msg`) |
| Redrawing without diffing causes visible artifacts | Three shipped bugs: `cce8f41`, `0233a64`, `489c99a` (docs/pad-timing.md §5) |
| Timed behavior must be epoch-anchored, wraparound-safe | docs/pad-timing.md §3 (`ticks_diff`/`ticks_add`, never raw comparisons) |
| Loop rate is unmeasured and unthrottled | docs/pad-timing.md §1 — animation needs its own frame clock |
| Host-side tests never import displayio | `tests/` are pure pytest over `shared/` and daemon modules |
| Pre-baked assets are the house pattern | Coach committed generated WAVs plus a test asserting bytes match the generator |

**No daemon changes.** Every input the OLED needs already crosses the link.

## 3. Architecture: layered scene graph

`displayio` is retained-mode: the panel only refreshes regions whose backing
`Bitmap`/`TileGrid` was dirtied. Layering is therefore free while layers are
static — a hidden layer or an unchanged badge costs zero work per tick.

One root `displayio.Group`, children bottom-to-top:

```
root
├── rain layer      (Group; hidden when ringing)
├── bell wall layer (Group; hidden when calm)
├── marquee layer   (Group; hidden except ~600ms after a ws switch)
└── badge layer     (Group; children individually hidden)
```

State selection is a hidden-flag flip, never a root_group swap and never a
clear-then-repaint.

### 3.1 Module split (testability rule)

All decidable logic is pure Python with no displayio import, so pytest covers
it on the host:

- `shared/km_weather.py` — the state machine and layout math:
  - `weather(urgent, link_up) -> "calm" | "ringing" | "nolink"`
  - `bell_order(stamps) -> [ws, ...]` — recency ordering, newest first
  - `wall_layout(order) -> [(ws, size, x, y), ...]` — numeral placement
  - `RainField` — column positions/advancement as pure data (which cells
    turn on/off this frame), seeded, screen-size-parameterized
  - marquee position as a pure function of elapsed ms
- `firmware/pad/ui.py` — rewritten: thin displayio edge. Owns the groups,
  bitmaps, and TileGrids; translates km_weather's outputs into pixel/tile
  mutations; enforces the diff discipline.

Cockpit keeps only bookkeeping: recency stamps and event forwarding (§5).

## 4. The layers

### 4.1 Rain (calm + no-link)

Sparse Matrix rain: 8–10 active columns on a 6px grid, each a falling head
glyph with a dithered fade trail (trailing glyphs lose pixels via a fixed
mask as they age, then vanish). Glyphs come from a small pre-baked 1-bit
glyph strip (a couple dozen characters is plenty; visual texture, not text).

- Runs on the shared frame clock at ~10 fps (§6). Each frame touches only
  each column's head cell and oldest-trail cell — never a full-screen clear.
- `RainField` is deterministic given a seed; the firmware seeds it per boot.
  (CircuitPython's `random` module suffices; determinism matters only for
  tests, which pass a fixed seed.)
- No-link differs from calm only by the corner tag; the rain keeps falling.
  Replaces today's `idle_card()`.

### 4.2 Bell wall (ringing)

- Most recent bell: large numeral, left field, ~48px tall.
- Other ringing workspaces: ~24px numerals in a row to its right, newest
  first, capped at 5 (there are only 6 workspaces; five ~14px-wide numerals
  fit the ~90px remaining width — a vertical stack would not fit 64px).
- Numerals are white-on-black at full contrast. No textures in v1 (parked —
  see §9).
- Digits are pre-baked 1-bit bitmaps in exactly two sizes, generated by
  `tools/gen_digits.py` from a chosen bold face, committed, with a test
  asserting committed bytes match the generator (the Coach WAV pattern).
  Ten glyphs × two sizes; no BDF font loading, no terminalio.
- Layout changes (bell added/cleared/reordered) repaint by difference:
  compute the new placement list, compare with the last-drawn list, and only
  touch changed regions.

### 4.3 Marquee (transient)

On workspace switch (`ws.active` changes): the new workspace's large numeral
wipes horizontally across the screen over ~600ms, then the layer hides.
Epoch-anchored: capture `ticks_ms` at trigger, position is a pure function of
elapsed; a new switch during playback restarts the epoch with the new
numeral. Reuses the 48px digit assets. Runs on the frame clock; hidden
otherwise, costing nothing.

### 4.4 Badges (persistent overlay)

Top-right, drawn over everything: inverted `REC` block while
`flags.screencast`; `[submap]` beside it while a submap is active, dropped
when the two would collide. Small text is acceptable here — badges are
alarms, not reading material, and the inverted block reads as a shape.
`no link` tag (bottom-right) belongs to this layer too and shows only in the
no-link state.

## 5. Data flow and Cockpit changes

Cockpit's `_draw_text` and its text caches disappear. In their place:

- On every `ws` message: stamp newly-urgent workspaces
  (`stamps[ws] = ticks_ms()` on first sight), delete stamps for cleared
  ones; if `active` changed, fire the marquee. Pass
  `bell_order(stamps)`, weather, and flags to the screen.
- `Screen` API (called only with changed values; Screen still self-defends
  by diffing internally):
  - `set_weather(state)` — flips layer visibility
  - `set_bells(ordered_ws_list)` — repaints the wall by difference
  - `marquee(ws)` — starts the wipe
  - `set_flags(rec, submap)` — badge layer
  - `tick(now)` — advances rain/marquee via the frame clock
- Recency stamps live in Cockpit (it owns link state and messages); layout
  lives in km_weather; pixels live in ui. Each piece is testable or trivially
  inspectable alone.

Stamps use `ticks_ms` only for *ordering*, compared via `ticks_diff` against
a common reference — never raw comparison (pad-timing §3). On link loss the
stamps clear; the daemon's full snapshot on reconnect rebuilds urgency (order
degrades to snapshot order — acceptable, documented, and self-healing).

## 6. Frame clock and the timing rules

One animation clock for rain + marquee: an epoch captured at app show,
frames scheduled at `epoch + n·100ms` (~10 fps) via `ticks_add`/`ticks_diff`,
catch-up-not-drift on slow ticks (the Coach metronome pattern). Between
frames, `tick()` does nothing to the display.

Every layer obeys pad-timing §5: cache what was last written, touch hardware
only on change. Specifically: a calm screen with no rain movement scheduled
this pass costs zero writes; a static bell wall costs zero writes; hidden
layers cost zero writes. Nothing ever calls `fill(0)` on a live bitmap.

## 7. Error handling

- Link down → no-link weather; everything else keeps working (rain is
  standalone by design, matching the "unplug the daemon and the pad shows a
  quiet idle card instead of pretending" principle).
- Malformed/missing message fields: same posture as today — `dict.get` with
  defaults, no crashes.
- Digit asset load failure at boot: fall back to the old text header path is
  **not** kept; instead fail loud in the REPL (assets are committed and
  deploy-tested — a missing asset is a deploy bug, not a runtime condition).

## 8. Testing

- `tests/test_weather.py`: state machine truth table; recency ordering incl.
  tick wraparound; wall layout (1 bell, 2, 6, add/remove/reorder); marquee
  position function edges (0ms, 600ms, restart mid-flight); RainField
  determinism with a fixed seed and bounds (never writes outside 128×64,
  frame deltas touch only head+tail cells).
- `tests/test_digit_assets.py`: committed digit bitmaps byte-match
  `tools/gen_digits.py` output.
- Hardware bench pass (manual, like Coach's audio pass): flicker check in
  all three states, transition into/out of ringing, marquee over each base,
  REC badge over rain and over the wall.

## 9. Out of scope (parked, deliberate)

- Window-granularity bell info — workspace numerals only.
- Per-workspace dither textures / identity poster; theme-driven dither
  wallpaper (the colorhash-as-texture idea). Natural v2 of the bell wall.
- Rain reacting to activity (density/speed modulation).
- The knob (still unassigned).
- Any daemon/protocol change.
