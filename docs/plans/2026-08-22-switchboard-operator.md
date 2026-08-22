# Switchboard Operator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the MacroPad to a single switchboard app and rebuild the OLED as a twelve-cell legend that is readable without looking at the keys.

**Architecture:** Delete the volume and Coach subsystems outright, collapse the firmware app framework to one app, and move all new geometry into `shared/km_deck.py` as pure functions so pytest can reach it. The OLED becomes six `displayio.Label`s (header, focused window, four 20-character legend rows) over one 128×39 `Bitmap` carrying twelve 4×9 state gutters, painted by difference.

**Tech Stack:** CircuitPython 9 on an Adafruit MacroPad RP2040 (`displayio`, `terminalio`, `adafruit_display_text`, `adafruit_ticks`); Python 3 asyncio host daemon; pytest.

**Spec:** `docs/specs/2026-08-22-switchboard-operator-design.md`

**Prerequisite already complete:** spec §3.3 requires Coach's timing findings be extracted before any Coach file is deleted. Done in commit `1021d90` (`docs/pad-timing.md`). Do not delete Coach files if that document is missing.

## Global Constraints

- `shared/` runs on **both** CPython and CircuitPython. No `asyncio`, no `pathlib`, no f-strings-with-`=`, no `str.isalnum()` (absent on CircuitPython — use explicit character-range checks).
- **Never type a literal U+00B7 (or any PUA/non-ASCII glyph) into a tool call.** Write it as `"·"` and verify with `od -c` before committing.
- Raw `ticks_ms()` values are never compared directly. Use `adafruit_ticks.ticks_diff` / `ticks_add` (wraparound). See `docs/pad-timing.md` §3.
- **Never touch hardware unconditionally per tick.** `pixels.auto_write` is `True`, so every `pixels[i] = ...` drives the whole strip; and a `displayio.Bitmap` dirtied at loop frequency forces continuous panel refresh. Cache what was last written and write only the difference. See `docs/pad-timing.md` §5.
- No arithmetic in `firmware/` — CircuitPython does not run under pytest. Geometry and content rules live in `shared/km_deck.py`.
- Bitmaps are painted by difference, **never** `fill(0)`-then-repaint.
- The full test suite (`python3 -m pytest tests/ -q`) must pass at the end of every task. Baseline at plan start: **232 passed**.
- Firmware changes cannot be verified by tests. Any task touching `firmware/` ends with an explicit "unverified on hardware" note in its report.

## File Structure

| File | Responsibility after this plan |
|---|---|
| `shared/km_deck.py` | `Deck` state machine, wire message, **and** all legend/gutter/countdown geometry |
| `shared/km_text.py` | `header_line`, `marquee` (one caller each) |
| `shared/km_palette.py` | Key colors only; `INDEX_BINS`, `ws_key_color`, `ctx_key_color` removed |
| `firmware/pad/ui.py` | Label assignment + bitmap diff. No arithmetic |
| `firmware/pad/framework.py` | One app, no menu |
| `firmware/apps/cockpit.py` | State + tick loop + the re-key hold timer |
| `daemon/keymakerd/__main__.py` | Supervisor; volume/ledger/ctx removed, `rekey` added |

---

### Task 1: Delete Coach and collapse the app framework

Coach and the framework's app menu must go together: `firmware/code.py` imports `Coach`, and `run()` takes a list. Removing either alone leaves the tree broken.

**Files:**
- Delete: `firmware/apps/coach.py`, `firmware/pad/audio.py`, `shared/km_coach.py`, `daemon/keymakerd/coach_store.py`, `tools/make_drums.py`
- Delete: `tests/test_coach.py`, `tests/test_coach_store.py`, `tests/test_audio.py`, `tests/test_drums.py`
- Modify: `firmware/code.py`, `firmware/pad/framework.py`, `daemon/keymakerd/__main__.py`, `shared/km_palette.py`

**Interfaces:**
- Produces: `run(macropad, app)` — single app, not a list.

- [ ] **Step 1: Confirm the prerequisite exists**

Run: `test -f docs/pad-timing.md && echo OK`
Expected: `OK`. If missing, STOP and report BLOCKED — spec §3.3 makes this doc a precondition of deletion.

- [ ] **Step 2: Delete the files**

```bash
git rm firmware/apps/coach.py firmware/pad/audio.py shared/km_coach.py \
       daemon/keymakerd/coach_store.py tools/make_drums.py \
       tests/test_coach.py tests/test_coach_store.py tests/test_audio.py tests/test_drums.py
```

- [ ] **Step 3: Rewrite `firmware/code.py`**

```python
import supervisor

from adafruit_macropad import MacroPad

from apps.cockpit import Cockpit
from pad.framework import run

macropad = MacroPad()
supervisor.runtime.autoreload = True
run(macropad, Cockpit())
```

- [ ] **Step 4: Collapse `firmware/pad/framework.py`**

Change the signature to `def run(macropad, app):`. Delete `HOLD_MENU_MS`, `menu_idx`, `enc_tracker`, the long-press-opens-menu branch, the tap-switches-app branch, and the trailing `if menu_idx is not None:` menu-rendering block. Replace every `apps[active]` with `app`. The encoder switch handling reduces to:

```python
        macropad.encoder_switch_debounced.update()
        if macropad.encoder_switch_debounced.pressed:
            try:
                app.on_enc(True, now)
            except Exception as e:
                print("on_enc error:", repr(e))
        if macropad.encoder_switch_debounced.released:
            try:
                app.on_enc(False, now)
            except Exception as e:
                print("on_enc error:", repr(e))
```

Add `on_enc(self, pressed, now)` to the `App` base class as a `pass` stub, and delete `on_click` from it. Keep the `ledtest` branch and the `hello` send on startup exactly as they are; change the `hello` payload's `"app"` field to `app.name`.

Delete the `if menu_idx is None:` guard around the key-event dispatch and around `app.tick(now)` — there is no menu to guard against.

- [ ] **Step 5: Remove Coach from the daemon**

In `daemon/keymakerd/__main__.py`: delete the `from .coach_store import CoachStore` import, `self.coach = CoachStore(...)`, the `self.link.send(self.coach.state_msg())` line in `_on_link_up`, `_coach_session`, and the `elif t == "coach":` branch in `_on_pad_msg`.

- [ ] **Step 6: Remove Coach's palette helper**

In `shared/km_palette.py`, delete `INDEX_BINS`. Coach was its only caller.

Verify first: `grep -rn "INDEX_BINS" --include=*.py . | grep -v __pycache__` must return nothing after the Coach files are gone.

- [ ] **Step 7: Run the suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, with a lower count than 232 (Coach's tests are gone). Record the new number.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: delete Coach and collapse the app framework to one app

Findings preserved in docs/pad-timing.md (1021d90) before deletion, per
spec section 3.3. The framework's app menu goes with it: with one app the
menu is unreachable UI, and both encoder gestures fall free."
```

---

### Task 2: Delete volume, and the knob's second mode

**Files:**
- Delete: `daemon/keymakerd/volume.py`, `tests/test_volume.py`
- Modify: `daemon/keymakerd/__main__.py`, `shared/km_deck.py`, `firmware/apps/cockpit.py`
- Test: `tests/test_deck.py`, `tests/test_supervisor.py`

**Interfaces:**
- Produces: `Deck.message(self, page, colors, focused=None, bells=(), name_max=14, ws_max=12)` — the `knob` positional parameter is gone, and the emitted dict no longer carries `"knob"`.

- [ ] **Step 1: Update the failing test first**

In `tests/test_deck.py`, find every call to `.message(` and drop the `knob` argument. Add this test:

```python
def test_message_no_longer_carries_a_knob_mode():
    # The knob has exactly one mode now (paging), so a mode field on the wire
    # would be a constant the pad has to branch on for no reason.
    d = km_deck.Deck()
    d.update([{"id": "tmux:@1", "ws": "a", "n": "1 sh"}])
    msg = d.message(0, {"a": "ff0000"})
    assert "knob" not in msg
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_deck.py -q`
Expected: FAIL — `TypeError` on the positional argument, or `assert "knob" not in msg`.

- [ ] **Step 3: Change `Deck.message`**

In `shared/km_deck.py`, change the signature to:

```python
    def message(self, page, colors, focused=None, bells=(), name_max=14, ws_max=12):
```

and remove `"knob": knob,` from the returned dict. Update the docstring's reference to the knob.

- [ ] **Step 4: Delete volume from the daemon**

```bash
git rm daemon/keymakerd/volume.py tests/test_volume.py
```

In `daemon/keymakerd/__main__.py`:
- change `from . import hyprland, ledtest, tmux, volume` to `from . import hyprland, ledtest, tmux`
- delete `self.muted = False`, `self.knob_mode = "vol"`, `_volume`, `on_knob_press`
- delete both `_, self.muted = await volume.status()` blocks (in `_on_link_up` and `_volume`'s former neighbours), including their surrounding `try`/`except (OSError, ValueError, IndexError)`
- `_flags_msg` becomes:

```python
    def _flags_msg(self):
        return {"t": "flags", "submap": self.state.submap,
                "screencast": self.state.screencast}
```

- `on_knob_turn` loses its mode guard:

```python
    def on_knob_turn(self, delta):
        pages = self.deck.page_count()
        self.deck_page = (self.deck_page + delta + pages) % pages
        self._resend_deck()
```

- in `_on_pad_msg`, the `dial` branch becomes `self.on_knob_turn(int(msg.get("d", 0)))`, and the `elif t == "click":` branch is deleted entirely
- `_deck_msg` drops the knob argument:

```python
        return self.deck.message(self.deck_page, colors,
                                 focused=focused, bells=bells)
```

- [ ] **Step 5: Update the supervisor tests**

In `tests/test_supervisor.py`, delete any test asserting on `knob_mode`, `muted`, `on_knob_press`, or a `click` message, and remove `"muted"` from any `flags` assertion.

- [ ] **Step 6: Strip the pad side**

In `firmware/apps/cockpit.py`:
- remove `"knob": "vol",` from the `self.deck` default dict
- delete the `MUTE` badge lines from `_draw_text`
- replace the mode line with `mode = "P%d/%d" % (d["page"] + 1, d["pages"])`
- change `self.flags = {"submap": "", "screencast": False, "muted": False}` to drop `"muted"`
- delete `on_click` from the class

- [ ] **Step 7: Run the suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: delete volume control and the knob's second mode

The knob only pages now, so the mode field on the wire was a constant the
pad branched on for nothing, and the encoder short-press that toggled it is
free."
```

---

### Task 3: Delete the attention ledger and the dead `ctx` message

**`_context()` is the shared poll loop.** Its body computes `ctx`, then calls `_poll_deck()` and `_poll_ledger()`. Deleting the function would silently take the deck poll with it — the deck would never update again and nothing would fail loudly. Rename and reduce it instead.

**Files:**
- Modify: `daemon/keymakerd/__main__.py`, `daemon/keymakerd/hyprland.py`, `firmware/apps/cockpit.py`
- Test: `tests/test_supervisor.py`

- [ ] **Step 1: Reduce the poll loop**

Replace `_context` with:

```python
    async def _poll_loop(self):
        while True:
            await asyncio.sleep(CTX_POLL_S)
            await self._poll_deck()
```

and change the `run()` gather from `self._context()` to `self._poll_loop()`.

- [ ] **Step 2: Delete the ledger**

In `daemon/keymakerd/__main__.py` delete: `_ledger_none`, `_ledger_msg`, `LEDGER_MAX_CLAUDES`, `LEDGER_MAX_BELLS`, `LEDGER_MAX_BYTES` and their comment block, `self.ledger = _ledger_none()`, `self.link.send(self.ledger)` in `_on_link_up`, and `_poll_ledger`.

- [ ] **Step 3: Delete `ctx`**

Delete `_ctx_none`, `self.ctx = _ctx_none()`, and `self.link.send(self.ctx)` in `_on_link_up`.

In `daemon/keymakerd/hyprland.py`, delete `win_msg` (the `{"t": "win", ...}` builder) — it is never sent.

In `shared/km_palette.py`, delete `ws_key_color` and `ctx_key_color`. They served the pre-switchboard split deck and die with `ctx`.

Verify first: `grep -rn "ws_key_color\|ctx_key_color" --include=*.py . | grep -v __pycache__` — if anything outside `tests/test_palette.py` still calls them, STOP and report rather than deleting. Remove their tests along with them.

- [ ] **Step 4: Check for orphans**

Run each and confirm no hits outside `__pycache__`:

```bash
grep -rn "list_claude_panes\|list_bells\|list_windows\b" --include=*.py . | grep -v __pycache__
```

`tmux.list_windows` / `list_bells` / `list_claude_panes` may now be unused. Delete any that have **zero** remaining callers, along with their tests. Do not delete one that `deck_bells` or `list_deck_windows` still calls — check before removing.

- [ ] **Step 5: Strip the pad side**

In `firmware/apps/cockpit.py`: delete `self.ledger`, the `elif t == "ledger":` branch in `on_msg`, `self.win` and the `elif t == "win":` branch, and the whole `claudes`/`bells`/`waiting`/`busy` block that computes `line1` and `line2`. Leave `line1`/`line2` unset for now — Task 7 replaces them. Set both to `""` so the screen is blank rather than stale.

- [ ] **Step 6: Run the suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete the attention ledger and the dead ctx message

_context() was the shared poll loop, not a ctx-only task -- deleting it
outright would have taken _poll_deck with it and stopped the deck updating
silently. Renamed to _poll_loop and reduced to the deck poll."
```

---

### Task 4: Legend and gutter geometry in `shared/km_deck.py`

All four pure functions the firmware will need. No firmware changes in this task.

**Files:**
- Modify: `shared/km_deck.py`
- Test: `tests/test_deck.py`

**Interfaces:**
- Produces:
  - `CELL_CHARS = 6`, `LEGEND_COLS = 3`, `LEGEND_ROWS = 4`
  - `cell_label(ws, name)` → exactly 6 characters
  - `legend_row(labels, row)` → exactly 20 characters
  - `gutter_pixels(states, blink, y0=0)` → `set` of `(x, y)`, bitmap-local
  - `countdown_text(elapsed_ms)` → `str` or `None`
  - `REKEY_START_MS = 500`, `REKEY_STEP_MS = 1000`, `REKEY_FIRE_MS = 3500`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deck.py`:

```python
def test_cell_label_is_always_exactly_six_characters():
    # The legend row packs three cells at a fixed 7-character pitch; a label of
    # any other length shifts every column to its right.
    for ws, name in [("mirepoix", "2 recipe-page-redesign"), ("a", "x"),
                     ("", ""), ("colorhash", "1 color-hash")]:
        assert len(km_deck.cell_label(ws, name)) == 6


def test_cell_label_keeps_the_tmux_index_as_a_disambiguator():
    # Window names arrive as "<index> <name>", and the index is the most
    # distinguishing thing about two windows in the same session.
    assert km_deck.cell_label("mirepoix", "2 recipe-page") == "mir" + "·" + "2r"
    assert km_deck.cell_label("mirepoix", "3 alias-bug") == "mir" + "·" + "3a"


def test_cell_label_pads_a_short_session_rather_than_shifting_columns():
    assert km_deck.cell_label("a", "1 sh") == "a  " + "·" + "1s"


def test_legend_row_is_twenty_characters_with_gaps_at_6_and_13():
    labels = ["ab" + str(i) + "de" + str(i) for i in range(12)]
    row = km_deck.legend_row(labels, 1)
    assert len(row) == 20
    assert row[6] == " " and row[13] == " "
    assert row[0:6] == labels[3] and row[7:13] == labels[4] and row[14:20] == labels[5]


def test_legend_row_renders_missing_slots_as_blanks():
    row = km_deck.legend_row(["abcdef"], 0)
    assert row == "abcdef" + " " * 14


def test_gutter_pixels_empty_state_lights_nothing():
    assert km_deck.gutter_pixels(["empty"] * 12, True) == set()


def test_gutter_pixels_live_is_a_single_column():
    lit = km_deck.gutter_pixels(["live"] + ["empty"] * 11, True)
    assert lit == {(1, y) for y in range(9)}


def test_gutter_pixels_ghost_is_a_dotted_column():
    lit = km_deck.gutter_pixels(["ghost"] + ["empty"] * 11, True)
    assert lit == {(1, y) for y in range(0, 9, 2)}


def test_gutter_pixels_focused_is_hollow_and_bell_is_solid():
    hollow = km_deck.gutter_pixels(["focused"] + ["empty"] * 11, True)
    solid = km_deck.gutter_pixels(["bell"] + ["empty"] * 11, True)
    assert len(solid) == 4 * 9
    assert hollow < solid            # a proper subset: outline inside the block
    assert (2, 4) in solid and (2, 4) not in hollow


def test_gutter_pixels_bell_goes_dark_on_the_off_phase():
    assert km_deck.gutter_pixels(["bell"] + ["empty"] * 11, False) == set()


def test_gutter_pixels_places_each_slot_in_the_key_grid():
    # Slot i lives at row i//3, column i%3 -- the MacroPad's physical layout.
    lit = km_deck.gutter_pixels(["empty"] * 4 + ["live"] + ["empty"] * 7, True)
    assert lit == {(1 + 42, y) for y in range(10, 19)}


def test_countdown_text_boundaries():
    assert km_deck.countdown_text(0) is None
    assert km_deck.countdown_text(499) is None
    assert km_deck.countdown_text(500) == "RE-KEY IN 3"
    assert km_deck.countdown_text(1499) == "RE-KEY IN 3"
    assert km_deck.countdown_text(1500) == "RE-KEY IN 2"
    assert km_deck.countdown_text(2500) == "RE-KEY IN 1"
    assert km_deck.countdown_text(3499) == "RE-KEY IN 1"
    assert km_deck.countdown_text(3500) == "RE-KEYING"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_deck.py -q`
Expected: FAIL — `AttributeError: module 'km_deck' has no attribute 'cell_label'`.

- [ ] **Step 3: Implement**

Append to `shared/km_deck.py`:

```python
# ---- OLED legend geometry (spec section 5.3-5.5) ---------------------------
# Here rather than in firmware/pad/ui.py so it is unit-testable: CircuitPython
# does not run under pytest, so any arithmetic left in the firmware is
# arithmetic nobody can check.

CELL_CHARS = 6          # "sss<sep>nn"
LEGEND_COLS = 3
LEGEND_ROWS = 4
CELL_SEP = "·"     # MIDDLE DOT -- never typed literally, see the plan
GUTTER_W = 4
GUTTER_H = 9
GUTTER_PITCH_X = 42     # exactly seven 6px character cells
GUTTER_PITCH_Y = 10
GUTTER_X0 = 1


def _alnum2(s):
    """First two alphanumerics of `s`, right-padded to two.

    str.isalnum() is absent on CircuitPython, so the class test is explicit.
    """
    out = ""
    for c in s:
        if ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9"):
            out += c
            if len(out) == 2:
                break
    return (out + "  ")[:2]


def cell_label(ws, name):
    """One legend cell: three of the workspace, a separator, two of the window.

    Always exactly CELL_CHARS characters -- the legend row packs three cells at a
    fixed 7-character pitch, so a short label would shift every column right of
    it. Window names arrive as "<index> <name>", so the first two alphanumerics
    are the tmux index plus one letter, which is the most distinguishing thing
    about two windows in the same session.
    """
    return (ws[:3] + "   ")[:3] + CELL_SEP + _alnum2(name)


def legend_row(labels, row):
    """One 20-character legend row: three cells separated by single spaces.

    Character 6 and character 13 are always those spaces. They overlap the next
    column's gutter on screen, which is harmless precisely because they are blank.
    """
    out = []
    for col in range(LEGEND_COLS):
        i = row * LEGEND_COLS + col
        out.append(labels[i] if i < len(labels) else " " * CELL_CHARS)
    return " ".join(out)


def gutter_pixels(states, blink, y0=0):
    """Lit pixels of all twelve state gutters, bitmap-local, as a set of (x, y).

    Five states told apart by SHAPE, not brightness: a one-bit panel has no hue
    to spare and no intensity either, so shape is the only channel left.

    Returned as a whole frame so the caller can paint the DIFFERENCE against the
    previous frame. Never clear and repaint: displayio's auto_refresh is on, and
    a cleared bitmap is a blank frame the panel can scan out. See
    docs/pad-timing.md section 5.
    """
    lit = set()
    for i, st in enumerate(states[:LEGEND_COLS * LEGEND_ROWS]):
        x = GUTTER_X0 + (i % LEGEND_COLS) * GUTTER_PITCH_X
        y = y0 + (i // LEGEND_COLS) * GUTTER_PITCH_Y
        if st == "live":
            for dy in range(GUTTER_H):
                lit.add((x, y + dy))
        elif st == "ghost":
            for dy in range(0, GUTTER_H, 2):
                lit.add((x, y + dy))
        elif st == "focused":
            for dx in range(GUTTER_W):
                lit.add((x + dx, y))
                lit.add((x + dx, y + GUTTER_H - 1))
            for dy in range(GUTTER_H):
                lit.add((x, y + dy))
                lit.add((x + GUTTER_W - 1, y + dy))
        elif st == "bell" and blink:
            for dx in range(GUTTER_W):
                for dy in range(GUTTER_H):
                    lit.add((x + dx, y + dy))
    return lit


REKEY_START_MS = 500     # below this a hold shows nothing: a brush is not intent
REKEY_STEP_MS = 1000
REKEY_FIRE_MS = 3500


def countdown_text(elapsed_ms):
    """Focused-row text during a re-key hold, or None when the row is unchanged.

    Hold-to-confirm rather than a plain long-press: re-keying reorders the whole
    board, and sticky allocation means there is no undo.
    """
    if elapsed_ms < REKEY_START_MS:
        return None
    if elapsed_ms >= REKEY_FIRE_MS:
        return "RE-KEYING"
    return "RE-KEY IN %d" % (3 - (elapsed_ms - REKEY_START_MS) // REKEY_STEP_MS)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Verify the separator byte**

Run: `grep -n 'CELL_SEP' shared/km_deck.py | head -1` then
`python3 -c "import sys; sys.path.insert(0,'shared'); import km_deck; print(repr(km_deck.CELL_SEP), km_deck.CELL_SEP.encode('utf8'))"`
Expected: `'·' b'\xc2\xb7'` — the escape resolved to one MIDDLE DOT, not a literal pasted glyph or a mojibake pair.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(deck): legend, gutter and countdown geometry

Pure and unit-tested, because CircuitPython does not run under pytest and
arithmetic left in firmware is arithmetic nobody can check -- a rule that has
already caught two real bugs here.

Five gutter states told apart by shape rather than brightness: the OLED is one
bit deep, so it has neither hue nor intensity to spare."
```

---

### Task 5: Put the focused window on the wire

**Files:**
- Modify: `shared/km_deck.py`
- Test: `tests/test_deck.py`

**Interfaces:**
- Consumes: `Deck.message(self, page, colors, focused=None, bells=(), ...)` from Task 2.
- Produces: the `deck` message gains `"focus"` — a string, `""` when nothing is focused.

- [ ] **Step 1: Write the failing test**

```python
def test_message_carries_the_focused_window_even_when_it_is_off_page():
    # The focused window is exactly what you want named while you are paging
    # AWAY from it, so this cannot be derived pad-side from `slots`.
    d = km_deck.Deck()
    wins = [{"id": "tmux:@%d" % i, "ws": "ws%d" % i, "n": "%d w" % i}
            for i in range(14)]
    d.update(wins)
    msg = d.message(0, {}, focused="tmux:@13")
    assert msg["focus"] == "ws13 13 w"
    assert len(msg["slots"]) == 12                   # page 0 only; @13 is on page 1


def test_message_focus_is_blank_when_nothing_is_focused():
    d = km_deck.Deck()
    d.update([{"id": "tmux:@1", "ws": "a", "n": "1 sh"}])
    assert d.message(0, {})["focus"] == ""


def test_message_focus_is_trimmed_to_the_screen_width():
    d = km_deck.Deck()
    d.update([{"id": "tmux:@1", "ws": "a" * 30, "n": "1 " + "b" * 30}])
    assert len(d.message(0, {}, focused="tmux:@1")["focus"]) <= 21
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_deck.py -q`
Expected: FAIL — `KeyError: 'focus'`.

- [ ] **Step 3: Implement**

In `Deck.message`, before the `return`, add:

```python
        # Composed here from _last rather than from `slots`, because the focused
        # window may be on a page the user is not looking at -- which is exactly
        # when naming it is most useful. _last holds every window regardless of
        # page, so this costs nothing extra.
        fmeta = self._last.get(focused) if focused else None
        focus = ("%s %s" % (fmeta["ws"], fmeta["n"]))[:FOCUS_MAX] if fmeta else ""
```

and add `"focus": focus,` to the returned dict. Define `FOCUS_MAX = 21` near `SLOTS_PER_PAGE` with a comment that 21 is the OLED's column count.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Check the wire budget**

Run:

```bash
python3 - <<'PY'
import sys; sys.path.insert(0,'shared')
import km_deck, km_proto
d = km_deck.Deck()
d.update([{"id":"tmux:@%d"%i,"ws":"w"*12,"n":"%d %s"%(i,"n"*14)} for i in range(12)])
m = d.message(0, {"w"*12:"ffffff"}, focused="tmux:@0")
print(len(km_proto.encode(m)), "bytes")
PY
```

Expected: comfortably under 2048 (`LineCodec`'s cap). Record the number. If it exceeds 2048, STOP and report — the message would be discarded whole.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(deck): name the focused window on the wire

Composed in Deck.message from _last, not derived pad-side from slots: the
focused window may be on a page you are not looking at, which is exactly when
naming it earns its row."
```

---

### Task 6: Rebuild the OLED surface

**Files:**
- Modify: `firmware/pad/ui.py`

**Interfaces:**
- Consumes: `km_deck.gutter_pixels`, `km_deck.LEGEND_ROWS`.
- Produces: `Screen.set_header(text)`, `Screen.set_focus(text)`, `Screen.set_legend(rows)`, `Screen.set_gutters(lit)`, `Screen.idle_card()`. `set_minimap` is removed.

- [ ] **Step 1: Rewrite `firmware/pad/ui.py`**

```python
"""OLED layout: inverted header / focused window / four legend rows over a
state-gutter bitmap. Spec section 5."""
import displayio
import terminalio
from adafruit_display_text import label

import km_deck

WIDTH_CHARS = 21   # 128px / 6px font
MAP_Y = 23         # bitmap origin; rows 23-61
MAP_H = 39


class Screen:
    def __init__(self, display):
        self.group = displayio.Group()
        self.header = label.Label(terminalio.FONT, text="", x=0, y=6,
                                  color=0x000000, background_color=0xFFFFFF)
        self.focus = label.Label(terminalio.FONT, text="", x=0, y=18)
        # Legend text sits at x=5 so the 4px state gutter at x=1 stays clear.
        # Rows are 10px apart, matching km_deck.GUTTER_PITCH_Y.
        self.rows = [label.Label(terminalio.FONT, text="", x=5, y=29 + i * 10)
                     for i in range(km_deck.LEGEND_ROWS)]
        self.group.append(self.header)
        self.group.append(self.focus)
        for r in self.rows:
            self.group.append(r)
        self.map_bmp = displayio.Bitmap(128, MAP_H, 2)
        pal = displayio.Palette(2)
        pal[0] = 0x000000
        pal[1] = 0xFFFFFF
        self.group.append(displayio.TileGrid(self.map_bmp, pixel_shader=pal,
                                             x=0, y=MAP_Y))
        # Lit pixels currently on the bitmap, so set_gutters can paint the
        # difference rather than clear and repaint. See km_deck.gutter_pixels.
        self._lit = set()
        display.root_group = self.group

    def set_header(self, text):
        # space-pad so the inverted bar always spans the full width
        t = text[:WIDTH_CHARS]
        self.header.text = t + " " * (WIDTH_CHARS - len(t))

    def set_focus(self, text):
        self.focus.text = text[:WIDTH_CHARS]

    def set_legend(self, rows):
        for i, r in enumerate(self.rows):
            r.text = rows[i] if i < len(rows) else ""

    def set_gutters(self, lit):
        """Paint the DIFFERENCE against what is already on the bitmap.

        Never a fill(0) first: displayio refreshes the panel on its own schedule
        (auto_refresh is on), so a clear-then-repaint hands it a blank frame to
        scan out, which is what reads as a flicker. An unchanged frame writes
        nothing at all.
        """
        for x, y in self._lit - lit:
            self.map_bmp[x, y] = 0
        for x, y in lit - self._lit:
            self.map_bmp[x, y] = 1
        self._lit = lit

    def idle_card(self):
        self.set_header("keymaker")
        self.focus.text = "no link"
        self.set_legend([""] * km_deck.LEGEND_ROWS)
        # Clear the gutters too: with the link down the deck they describe is
        # stale, and leaving them lit beside "no link" claims windows we can no
        # longer see.
        self.set_gutters(set())
```

> **The firmware is transiently broken between Tasks 6 and 7.** This task deletes
> `set_minimap`, `line1`, `line2` and `footer`, which `cockpit.py` still calls
> until Task 7 replaces them. The test suite cannot see this — `firmware/` is not
> under test — so it will pass while the pad would crash on boot. **Do not deploy
> between these two tasks.** They are separate because they have separate review
> surfaces, not because either is independently shippable.

- [ ] **Step 2: Confirm nothing still calls the old API**

Run: `grep -rn "set_minimap\|screen.line1\|screen.line2\|screen.footer" --include=*.py firmware/ | grep -v __pycache__`
Expected: hits only in `firmware/apps/cockpit.py`, which Task 7 rewrites. Note them for that task.

- [ ] **Step 3: Run the suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS — `firmware/` is not under test, so this only confirms nothing else broke.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(ui): rebuild the OLED as a legend over a gutter bitmap

Six labels: header, focused window, and four 20-character legend rows. The
blinking part (gutters, in the bitmap) is deliberately a different object from
the expensive part (names, in the labels), so a blink touches 36 pixels and
zero labels.

Unverified on hardware."
```

---

### Task 7: Render the legend in Cockpit

**Files:**
- Modify: `firmware/apps/cockpit.py`

**Interfaces:**
- Consumes: `Screen.set_header/set_focus/set_legend/set_gutters`, `km_deck.cell_label`, `km_deck.legend_row`, `km_deck.gutter_pixels`, `km_text.header_line`, `km_text.marquee`.

- [ ] **Step 1: Replace `_draw_text`**

```python
    def _draw_text(self, now):
        if not self.link.up:
            self.screen.idle_card()
            self._header_text = self._focus_text = None
            self._legend = None
            return
        d = self.deck
        blink = (now // 450) % 2 == 0

        badges = []
        if self.flags["screencast"]:
            badges.append("REC")
        if self.flags["submap"]:
            badges.append("[" + self.flags["submap"] + "]")
        mode = "P%d/%d %dw" % (d["page"] + 1, d["pages"], d["total"])
        header = header_line("nexus", badges, mode, WIDTH_CHARS)
        if header != self._header_text:
            self._header_text = header
            self.screen.set_header(header)

        # A re-key countdown owns the focused row while a hold is in progress.
        focus = self._countdown if self._countdown is not None else d["focus"]
        focus = marquee(focus, WIDTH_CHARS, now)
        if focus != self._focus_text:
            self._focus_text = focus
            self.screen.set_focus(focus)

        labels = [" " * km_deck.CELL_CHARS] * km_deck.SLOTS_PER_PAGE
        states = ["empty"] * km_deck.SLOTS_PER_PAGE
        for slot in d["slots"]:
            ws = d["ws"][slot["c"]][0]
            labels[slot["i"]] = km_deck.cell_label(ws, slot["n"])
            states[slot["i"]] = slot["s"]
        legend = [km_deck.legend_row(labels, r) for r in range(km_deck.LEGEND_ROWS)]
        # Labels are rewritten only when the deck actually changes; the blink
        # below never touches them. See docs/pad-timing.md section 5.
        if legend != self._legend:
            self._legend = legend
            self.screen.set_legend(legend)
        self.screen.set_gutters(km_deck.gutter_pixels(states, blink))
```

- [ ] **Step 2: Update the cached fields**

In `__init__` and `on_show`, replace `self._map_sig`, `self._line1_text`, `self._line2_text` with `self._focus_text = None` and `self._legend = None`. Add `self._countdown = None` and `self._enc_down = None` to `__init__` only (not `on_show` — a hold in progress should not survive a repaint).

Update the `self.deck` default dict to include the fields `_draw_text` now reads:

```python
        self.deck = {"t": "deck", "page": 0, "pages": 1, "total": 0, "focus": "",
                     "ws": [], "slots": [], "map": [0], "bells": []}
```

- [ ] **Step 3: Add `total` to the wire**

`mode` reads `d["total"]`. Add it in `shared/km_deck.py`'s `Deck.message` return dict as `"total": len(self.slots),` and add this test to `tests/test_deck.py`:

```python
def test_message_reports_the_total_window_count_across_all_pages():
    d = km_deck.Deck()
    d.update([{"id": "tmux:@%d" % i, "ws": "a", "n": "%d w" % i} for i in range(14)])
    assert d.message(0, {})["total"] == 14
```

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(cockpit): render the twelve-cell legend

Names come from the deck message and are written only when the deck changes;
the 450ms blink drives gutters in the bitmap and never touches a label.

Unverified on hardware."
```

---

### Task 8: The re-key gesture

**Files:**
- Modify: `firmware/apps/cockpit.py`, `daemon/keymakerd/__main__.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `km_deck.countdown_text`, `km_deck.REKEY_FIRE_MS`, `App.on_enc(pressed, now)` from Task 1.
- Produces: pad→daemon `{"t": "rekey"}`; `Supervisor.on_rekey()`.

- [ ] **Step 1: Write the failing daemon test**

In `tests/test_supervisor.py`:

```python
def test_rekey_clears_slots_and_ghosts_and_re_derives_in_order(tmp_path):
    # Sticky allocation means a window keeps its first slot for life, so this
    # is the ONLY way to reorder a board once assignments exist.
    sup = make_supervisor(tmp_path)
    sup.deck.slots = {"tmux:@9": 0, "tmux:@1": 1}
    sup.deck.ghosts = {5: {"ws": "gone", "n": "1 old"}}
    sup.deck._last = {"tmux:@9": {"ws": "b", "n": "1 x"},
                      "tmux:@1": {"ws": "a", "n": "1 y"}}
    sup._deck_render_args = ({}, None, [])
    sup._deck_wins = [{"id": "tmux:@1", "ws": "a", "n": "1 y"},
                      {"id": "tmux:@9", "ws": "b", "n": "1 x"}]
    sup.on_rekey()
    assert sup.deck.slots == {"tmux:@1": 0, "tmux:@9": 1}
    assert sup.deck.ghosts == {}
```

Adapt `make_supervisor` to whatever the existing tests in that file use to build a `Supervisor`; do not invent a new helper if one exists.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_supervisor.py -q`
Expected: FAIL — `AttributeError: 'Supervisor' object has no attribute 'on_rekey'`.

- [ ] **Step 3: Implement the daemon side**

In `_poll_deck`, after `wins = hyprland.deck_windows(...)`, cache them: `self._deck_wins = wins`. Initialise `self._deck_wins = []` in `__init__` beside `self._deck_twins`.

Add:

```python
    def on_rekey(self):
        """Drop every slot assignment and re-derive from workspace order.

        Ghosts go too: a re-key is a fresh board, and a completion marker kept
        against a slot that no longer means the same thing is worse than a lost
        one. No service restart -- clearing deck-slots.json by hand and bouncing
        the unit was only ever a way to reach this state.
        """
        self.deck.slots = {}
        self.deck.ghosts = {}
        self.deck.update(self._deck_wins)
        self.save_deck()
        self._resend_deck()
```

In `_on_pad_msg`, add `elif t == "rekey": self.on_rekey()`.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Implement the pad side**

In `firmware/apps/cockpit.py`:

```python
    def on_enc(self, pressed, now):
        if pressed:
            self._enc_down = now
            return
        # Released. Fire only if the hold ran the full countdown; anything
        # shorter is an abort, and an abort sends nothing at all.
        if self._enc_down is not None:
            held = ticks_diff(now, self._enc_down)
            if held >= km_deck.REKEY_FIRE_MS:
                self.link.send({"t": "rekey"})
        self._enc_down = None
        self._countdown = None

    def _tick_countdown(self, now):
        if self._enc_down is None:
            self._countdown = None
            return
        self._countdown = km_deck.countdown_text(ticks_diff(now, self._enc_down))
```

Call `self._tick_countdown(now)` at the top of `tick`, before `_draw_text`.

- [ ] **Step 6: Confirm the abort path sends nothing**

Read `on_enc` back and confirm there is exactly one `link.send` in it, inside the `held >= REKEY_FIRE_MS` branch. A re-key has no undo; an accidental brush must be silent.

- [ ] **Step 7: Run the suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: re-key the board from a held encoder press

Hold-to-confirm rather than a plain long-press: re-keying reorders every key
and sticky allocation gives it no undo, so a brush must be silent. Releasing
before the countdown finishes sends nothing.

No service restart -- clearing deck-slots.json and bouncing the unit was only
ever a way to reach this state.

Pad side unverified on hardware."
```

---

## Deploy and verify

Not a task — hardware verification is Chris's, and it cannot be automated.

```bash
cd ~/src/keymaker && ./system/deploy-firmware.sh && systemctl --user restart keymaker.service
```

Check, in order:

1. **Legend populates.** Twelve cells in the physical key arrangement, `sss·nn` per occupied slot, blanks elsewhere.
2. **Header** reads `nexus  P1/1 5w`, with `REC` appearing when screencasting.
3. **Focused row** names the focused window, and keeps naming it after paging away.
4. **Gutters** show four distinct shapes. A bell blinks; nothing else does.
5. **Knob** pages in one direction and wraps.
6. **Encoder short-press does nothing** — confirm no message and no visible change.
7. **Hold the encoder:** `RE-KEY IN 3 / 2 / 1`, then the board re-orders on release. Release at 2 and confirm **nothing** happens.
8. **Bright-room check:** the legend is readable when the keys are not.

Open questions this deploy settles, both from spec §11: whether six characters actually disambiguate, and whether a blinking 4×9 gutter reads strongly enough. Neither has a code answer.
