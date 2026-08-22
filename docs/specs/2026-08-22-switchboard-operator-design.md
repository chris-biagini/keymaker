# Switchboard Operator — Design

**Date:** 2026-08-22
**Status:** approved, pre-implementation
**Supersedes parts of:** `docs/specs/2026-08-15-cockpit-v2-design.md` (knob modes, app menu),
the switchboard spec at `~/oracle/docs/superpowers/specs/2026-08-22-macropad-switchboard-design.md`
(§8.1–8.2 OLED layout, §9 wire format)

> Spec location note: this file lives with the code it describes. The immediately
> preceding switchboard spec lives in the `oracle` repo instead, an artifact of
> which directory that session ran in. New keymaker specs go here.

## 1. Goal

Reduce the MacroPad to one job — a switchboard for every open terminal window —
and spend the freed hardware on doing that job better. Volume control and the
Coach drum trainer are removed outright. The OLED stops being a small index into
the keys and becomes a legend that stands on its own.

## 2. Motivating constraints

Two facts drive every decision below.

**The keys are not always legible.** In a bright room the RGB keys wash out while
the OLED stays readable. The OLED is therefore the more reliable of the two
surfaces, and gets the space accordingly.

**The OLED is one bit deep.** The colorhash identity that makes the keys legible
cannot cross to the screen — there is no hue. Every piece of identity the screen
conveys must be re-encoded as position, shape, or letters. This is the monochrome
form of the project's standing rule that hue is never the only channel.

## 3. Removals

### 3.1 Files deleted

| Area | Paths |
|---|---|
| Volume | `daemon/keymakerd/volume.py`, `tests/test_volume.py` |
| Coach | `firmware/apps/coach.py`, `shared/km_coach.py`, `daemon/keymakerd/coach_store.py`, `tools/make_drums.py`, `tests/test_coach.py`, `tests/test_coach_store.py`, `tests/test_drums.py` |
| Audio | `firmware/pad/audio.py`, `tests/test_audio.py` |

Any generated drum sample assets on the CIRCUITPY volume are removed by the next
firmware deploy; they are not tracked in the repo.

### 3.2 Code deleted

**`daemon/keymakerd/__main__.py`**

- `_volume`, `_coach_session`, `self.muted`
- `self.knob_mode`, `on_knob_press`, and the `knob_mode` branch in the `dial` handler
- the `coach` and `click` message handlers
- `_poll_ledger`, `_ledger_msg`, `_ledger_none`, `self.ledger`, and the `ledger` send
- `_ctx_none`, `self.ctx`, and the `ctx` send — the pre-switchboard split-deck
  message, unused since the switchboard shipped

> `_context()` is **not** simply deleted. It is the shared poll loop: its body
> computes `ctx`, then calls `_poll_deck()` and `_poll_ledger()`. Deleting the
> function would silently take the deck poll with it. It is renamed `_poll_loop()`
> and reduced to `sleep(CTX_POLL_S)` plus `await self._poll_deck()`.

**`daemon/keymakerd/hyprland.py`**

- `win_msg` (`{"t": "win", ...}`) — never sent; the focused window now travels in
  `deck` (§7.1)

**`shared/km_palette.py`**

- `ws_key_color`, `ctx_key_color`, `INDEX_BINS` — Coach was `INDEX_BINS`'s last caller

**`firmware/pad/framework.py`**

- `menu_idx` and the entire app-menu branch: the long-press-opens-menu path, the
  tap-switches-app path, the menu rendering block
- `run(macropad, apps)` becomes `run(macropad, app)` — one app, no list

**`firmware/apps/cockpit.py`**

- the `MUTE` badge
- the knob-mode string (`"VOL"` / `"P1/2"` selection); the mode is always the page

`REC` (screencast) is retained: it reflects Hyprland state and is unrelated to
volume. `km_text.marquee` is retained; §5.2 gives it its one caller.

### 3.3 Knowledge preserved before deletion

Coach was this project's only subsystem with hard timing requirements. Its
findings are extracted to `docs/pad-timing.md` **before** any Coach file is
deleted — a prerequisite of the removal, not a follow-up to it.

The extraction corrected an assumption in an earlier draft of this spec: Coach
produced no measurement of the loop rate, and **no such figure exists anywhere in
this repo**. `framework.py`'s `run()` is a bare `while True:` with no sleep, so
there is no per-tick budget to stay inside. The governing rule is not "stay under
N ms" but **"never touch hardware unconditionally per tick"** — the lesson behind
`cce8f41`, `0233a64`, and `489c99a` alike. §5.6 is that rule applied.

## 4. Input model

| Input | Behavior |
|---|---|
| Key tap | Dismiss the ghost in that slot if there is one; otherwise focus that window |
| Key hold | Nothing — `act == "hold"` is received and ignored |
| Knob turn | Change page (always — there is no other mode) |
| Encoder short-press | **Unassigned.** No handler, no message sent |
| Encoder long-press | Re-key countdown (§6) |

The short-press is deliberately left dead rather than given a weak assignment.

## 5. OLED layout

128 × 64, 1-bit. `terminalio.FONT` advances 6px per character, giving 21 columns.
Labels occupy a 9px band from `y-6` to `y+2` relative to their `y`; the row
figures below are the resulting pixel rows.

### 5.1 Regions

| Rows | Region | Object |
|---|---|---|
| 0–8 | Header, inverted | `Label` (existing `header`) |
| 12–20 | Focused window | `Label` (existing `line1`) |
| 23–31 | Legend row 0 | `Label` |
| 33–41 | Legend row 1 | `Label` |
| 43–51 | Legend row 2 | `Label` |
| 53–61 | Legend row 3 | `Label` |
| 23–61 | State gutters | `Bitmap` (128 × 39 at y=23) |

Six labels total, and no per-cell labels. `ui.py` has four today (`header`,
`line1`, `line2`, `footer`): `line2` is repurposed as legend row 0, `footer` as
legend row 1, and two labels are added.

### 5.2 Header and focused row

Header is composed by the existing `km_text.header_line(host, badges, mode, width)`:

- host: `nexus`
- badges: `REC` if screencasting, `[submap]` if a Hyprland submap is active
- mode: `P<page>/<pages> <n>w`, where `<n>` is the total window count

`header_line` already guarantees the mode is never truncated and drops badges
under width pressure. That behavior is unchanged and is the reason header
composition is not inlined in firmware.

The focused row renders `deck["focus"]` (§7.1) through `km_text.marquee` when it
exceeds 21 columns, and as plain text when it does not. This is `marquee`'s only
remaining caller.

During a re-key countdown this row is replaced (§6).

### 5.3 Legend geometry

Three columns. Each cell is a 4px state gutter followed by six characters.

| | Gutter x | Text x |
|---|---|---|
| Column 0 | 1–4 | 5 |
| Column 1 | 43–46 | 47 |
| Column 2 | 85–88 | 89 |

Text origins are 42px apart, which is exactly seven 6px character cells. Each
legend row is therefore **one 20-character string**:

```
index:  0 1 2 3 4 5  6  7 8 9 10 11 12  13  14 15 16 17 18 19
        └── col 0 ──┘ sp └──── col 1 ────┘ sp └──── col 2 ────┘
```

Characters 6 and 13 are always spaces. They overlap the next column's gutter
(x 41–46 and 83–88 respectively), which is harmless because they are blank.

Slot `i` maps to row `i // 3`, column `i % 3` — the MacroPad's physical key
arrangement, so the screen is a scale model of what is under the fingers.

### 5.4 Cell text

Six characters: `sss` + `:` + `nn`, where `sss` is the first three characters
of the session name and `nn` the first two alphanumeric characters of the window
name. Examples: `mir:1i`, `col:1c`, `bon:1b`.

A sessionless terminal uses its window class in place of the session name.
An empty slot renders six spaces.

> The separator is ASCII `:`. `terminalio.FONT`'s coverage above ASCII has never
> been verified on this hardware, and `session:index` is tmux's own addressing
> convention, so the colon is both safer and more idiomatic than a middle dot.

The two alphanumerics are taken from the window name, which arrives as
`"<index> <name>"` — so they are the tmux window index plus one letter, which is
the most distinguishing pair available for two windows in the same session.

### 5.5 State gutters

Drawn in the bitmap, never in the labels. Five states, distinguished by **shape**
rather than brightness:

| State | Gutter |
|---|---|
| Empty | nothing |
| Live | 1px vertical line at the gutter's left edge |
| Ghost | 1px vertical line, dotted (every other pixel) |
| Focused | hollow 4 × 9 outline |
| Bell | solid 4 × 9 block, blinking at the existing 450ms phase |

Splitting gutters (bitmap) from names (labels) is load-bearing for performance:
a blink touches 36 bitmap pixels and zero labels. Labels are rewritten only when
the deck itself changes.

### 5.6 Painting discipline

The bitmap is painted by difference, never cleared and repainted — `displayio`
runs with `auto_refresh` on, so a cleared bitmap is a blank frame the panel can
scan out, which reads as a flicker. This is the discipline established by
`km_deck.minimap_pixels` and it carries forward unchanged: a pure function
computes the full set of lit pixels, and firmware writes only the difference.

## 6. Re-key

Clears every slot assignment and re-derives the board from current workspace
order. Sticky allocation means a window keeps its first slot for life, so this
is the only way to reorder a board once assignments exist.

**Gesture.** Hold the encoder.

| Elapsed | Focused row shows |
|---|---|
| 0–499ms | unchanged |
| 500ms | `RE-KEY IN 3` |
| 1500ms | `RE-KEY IN 2` |
| 2500ms | `RE-KEY IN 1` |
| 3500ms | fires; row reverts |

Releasing before 3500ms aborts: the row reverts and **no message is sent**. The
countdown is a hold-to-confirm, so an accidental brush cannot reorder the board.

**On fire** the pad sends `{"t": "rekey"}`. The daemon:

1. Clears `Deck.slots` and `Deck.ghosts`
2. Re-derives slots from `deck_windows` order (workspace id, then session, then
   window index)
3. Persists the new map to `deck-slots.json`
4. Pushes a fresh `deck` message

No service restart and no link drop. Ghosts are cleared because a re-key is a
fresh board; retaining completion markers against slots that no longer mean the
same thing would be worse than losing them.

## 7. Wire protocol

### 7.1 `deck` — daemon → pad

**Added:** `"focus"` — the focused window as a pre-trimmed display string
(`"<workspace> <window>"`), or `""` when nothing terminal is focused.

Sent at top level rather than derived pad-side from the focused slot, because the
focused window may be on a page the user is not viewing; deriving it from `slots`
would blank the row exactly when it is most wanted. Cost is roughly 25 bytes
against `LineCodec`'s 2048-byte cap, whose measured worst case is ~1028 bytes.

It is composed inside `Deck.message` from `Deck._last`, which already holds every
window's metadata regardless of page — so no daemon-side plumbing is needed and
the daemon's `_deck_msg` only loses its `knob` argument.

**Removed:** `"knob"` — there is only one knob mode.

All other fields are unchanged.

### 7.2 `flags` — daemon → pad

**Removed:** `"muted"`. Retains `"submap"` and `"screencast"`.

### 7.3 `rekey` — pad → daemon

`{"t": "rekey"}`. No payload.

### 7.4 Removed messages

`ctx`, `ledger`, `win` (daemon → pad); `click`, `coach` (pad → daemon).

## 8. Code structure

Geometry and content rules are pure functions in `shared/km_deck.py`, callable
from CPython under pytest and from CircuitPython on the pad:

| Function | Returns |
|---|---|
| `legend_row(slots, row)` | the 20-character string for one legend row |
| `cell_label(win)` | the six-character `sss:nn` abbreviation |
| `gutter_pixels(states)` | set of lit `(x, y)` for all twelve gutters |
| `countdown_text(elapsed_ms)` | `"RE-KEY IN 3"` / `2` / `1` / `None` |

`firmware/pad/ui.py` holds only label assignment and the bitmap diff.
`firmware/apps/cockpit.py` holds only state and the tick loop. No arithmetic
lives in firmware — CircuitPython does not run under pytest, so arithmetic left
there is arithmetic nobody can check. This rule has caught two real bugs in this
codebase already.

## 9. Testing

- Pure functions in `shared/` get direct unit tests, including a reference
  oracle for `gutter_pixels` in the style of `minimap_pixels`.
- `countdown_text` is tested at every boundary (499/500/1499/1500/2499/2500/3499/3500).
- Daemon `rekey` handling is tested through the existing supervisor test
  harness: slots and ghosts cleared, re-derived in workspace order, persisted,
  deck re-sent.
- Firmware rendering is verified on hardware. There is no emulator.

## 10. Out of scope

- Ghost decay (deferred previously; unchanged)
- Any use of the encoder short-press
- Widening cell text beyond six characters
- Per-cell text inversion for bells — impossible with row-level labels, and the
  fallback (§11) is a blinking gutter

## 11. Known risks

**Six characters may not disambiguate.** `mir:2r` and `mir:2re`
collapse to the same abbreviation when two windows in a session share a prefix.
The premise is that stable position plus a six-character hint is enough once the
board is learned. This is the assumption most likely to be wrong and the cheapest
to test: build the legend first, live with it, and only then decide whether to
widen cells at the cost of columns.

**A blinking gutter may not read as strongly as an inverted cell.** Row-level
labels make full-cell inversion impossible. If a 4 × 9 blinking block proves too
quiet next to a bright-room-washed key, the escalation is hand-blitting glyphs
from `terminalio.FONT` into the bitmap so firmware owns every pixel and can
invert freely. That is deliberately not in this spec's scope.
