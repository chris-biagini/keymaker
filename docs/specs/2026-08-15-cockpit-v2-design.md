# Cockpit v2 — Split Deck, tmux Context, Bell Notifications, Identity OLED

> **Terminal discovery superseded 2026-08-24:** The original `footguard-*`/`ws-*`
> class-derived association was replaced by the terminal-agnostic
> `tmux-local-clients` contract. The split-deck protocol, ordering, tap behavior,
> colors, and bell semantics remain historical foundations.

**Date:** 2026-08-15
**Status:** Approved design, pre-plan
**Builds on:** v1 foundation (PR #1, merged at `1bff9b4`) — Cockpit workspace deck,
keymakerd, JSON-lines protocol, per-workspace Okabe-Ito key colors.

## 1. Overview

Cockpit v2 turns the pad into a two-layer navigation surface:

- **Top half (keys 0–5):** Hyprland workspaces 1–6, exactly as v1 (tap = jump,
  hold = move window, per-workspace colors, urgent blink).
- **Bottom half (keys 6–11):** the tmux windows of the focused footguard
  session (tap = select window). Dark when no footguard window is focused.
- **OLED:** inverted identity header (`3 · macropad`), and a window legend in
  the main area that labels the bottom key rows.
- **Notifications:** a terminal bell (BEL, 0x07) anywhere in the stack bubbles
  up to blinking keys — top half for "another workspace needs you," bottom half
  for "another window in this session needs you."

Design constraints (Chris, 2026-08-15): universal primitives that compose the
unix way; as simple as possible but no simpler. Firefox is explicitly out of
scope for v2 (revisit later; without an extension it is send-only).

## 2. Verified environment facts

Everything below was checked live on nexus before this design was approved.

| Fact | Evidence |
|---|---|
| tmux server already passes bells through AND flags windows | `bell-action any`, `monitor-bell on`, `visual-bell off` (live `tmux show`) |
| The bell chain is severed at exactly one link | `~/.config/foot/foot.ini` has no `[bell]` section → foot default `urgent=no` swallows BEL |
| tmux sessions are 1:1 with workspace names | footguard (`~/.local/bin/footguard`): session = sanitized workspace label; window `app-id` = `footguard-<session>`. Live: `macropad`, `mirepoix`, `sysop` |
| The daemon already knows the focused window's class | v1 `win` msg: `{"t":"win","cls":...,"title":...}` |
| Workspace names carry resolved identity colors | `2·<span foreground='#E14B00'>mirepoix</span>` (live `hyprctl workspaces`) |
| The identity palette is a pure function of the name | `wsid_color`: `cksum(label) % 7` into a theme-aware palette (`~/oracle/scripts/workspace-identity-lib`) |
| Two hue-locked 7-bin palettes exist | `WSID_PALETTE_DARK=(F0A52D 5AB4FF C3FFE1 87875A 4B8796 E14B00 FFD2F0)`, `WSID_PALETTE_LIGHT=(C36900 0087E1 002D1E 3C3C00 003CC3 78694B 4B003C)`; bin order = canonical Okabe-Ito listing |
| tmux status bar already highlights belled windows | `window-status-bell-style "fg=red,bold"` in `~/.config/tmux/statusbar.conf` |
| statusbar.conf is shared with thinkpad and re-sources on every client attach | header comments in the file; `client-attached` hook in `~/.tmux.conf` |
| Hyprland 0.56.0 | `hyprctl version` |

## 3. Key layout

Physical grid is 3 wide × 4 tall, keys numbered 0–11 left-to-right,
top-to-bottom.

| Keys | Row | Meaning | Tap | Hold |
|---|---|---|---|---|
| 0–2 | 1 | workspaces 1–3 | `dispatch workspace N` | `movetoworkspacesilent N` |
| 3–5 | 2 | workspaces 4–6 | same | same |
| 6–8 | 3 | tmux windows 1–3 | `tmux select-window -t <session>:N` | reserved (no-op) |
| 9–11 | 4 | tmux windows 4–6 | same | reserved (no-op) |

Workspaces 7+ and tmux windows 7+ are off-pad (SUPER+num and M-num still reach
them). The pad keeps interpreting nothing: it sends `key(n, tap|hold)` as in
v1 and the **daemon** owns the split — future modes are daemon-only changes.

## 4. Protocol changes (additive)

v1 messages are unchanged; v1 firmware against a v2 daemon (or vice versa)
degrades gracefully because unknown fields and unknown `t` values are ignored.

**`ws` gains `names`** — clean labels stripped from the pango markup:

```json
{"t": "ws", "active": 3, "occupied": [2, 3], "urgent": [],
 "colors": {"2": "e14b00", "3": "5ab4ff"},
 "names":  {"2": "mirepoix", "3": "macropad"}}
```

**New `ctx` message** — bottom-half state, state-shaped like everything else,
included in the snapshot-on-connect, re-sent only on change:

```json
{"t": "ctx", "mode": "tmux", "session": "mirepoix",
 "items": [
   {"i": 1, "name": "vim",    "active": false, "bell": true},
   {"i": 2, "name": "server", "active": true,  "bell": false}
 ]}
```

- `mode` is `"tmux"` or `"none"`. In `"none"`, `session` is `null` and
  `items` is `[]`.
- `items` carries window indices 1–6 only, in index order; a session's
  windows 7+ are omitted.
- `bell` mirrors tmux `window_bell_flag`; `active` mirrors `window_active`.

Uplink is unchanged: `key(n, tap|hold)`, `dial`, `click`, `hello`.

## 5. Daemon changes

### 5.1 `daemon/keymakerd/tmux.py` (new module)

Thin wrapper over the tmux CLI, in the style of `volume.py`:

- `list_windows(session) -> list[dict] | None` — runs
  `tmux list-windows -t <session> -F '#{window_index}\t#{window_active}\t#{window_bell_flag}\t#{window_name}'`,
  parses to `[{"i", "name", "active", "bell"}]`, returns `None` on any
  failure (no server, session gone, non-integer index) — never raises.
- `select_window(session, i) -> bool` — runs
  `tmux select-window -t <session>:<i>`, returns success.

This is a subprocess-per-call CLI client, the same mechanism every tmux
status-bar integration uses. It never touches tmux server configuration.

### 5.2 `ContextWatcher` (new Supervisor task)

An asyncio task alongside `_hypr_events` / `_refresher` / `_pinger` — not a
new process, not a new systemd unit.

- Reads the focused window class from `HyprState`. Class `footguard-<session>`
  → tmux mode for that session; anything else → `mode: "none"`.
- While in tmux mode, polls `list_windows` at 1 Hz. Emits a `ctx` message only
  when the built state differs from the last sent one.
- On focus change away from footguard, emits `mode: "none"` once and stops
  polling (zero background cost in Firefox or on empty workspaces).
- `list_windows` returning `None` degrades to `mode: "none"`; the task never
  dies (guarded like `_volume` calls).

### 5.3 Workspace labels

`hyprland.py` gains `ws_label(name) -> str | None` beside `ws_color`:
strip tags (`<[^>]*>` → `""`), then a leading `<digits>·` prefix; a result
that is empty or equal to the workspace id string is `None` (unnamed
workspace). `HyprState.refresh` collects `names` the same way it collects
`colors`, and the change-comparison and `_ws_msg` include it.

### 5.4 Key dispatch

Supervisor key handling becomes:

- `n` in 0–5: workspace `n+1` — tap `workspace`, hold `movetoworkspacesilent`
  (unchanged from v1 except the range).
- `n` in 6–11: slot `n-5` — tap calls `tmux.select_window(session, slot)`
  using the ContextWatcher's current session; ignored when mode is `"none"`.
  Hold is a no-op (reserved).

## 6. Firmware changes

### 6.1 OLED (`firmware/pad/ui.py`, `firmware/apps/cockpit.py`)

Layout (128×64, 21 chars/line at the terminalio font):

```
┌─────────────────────┐
│█3 · macropad████████│  header: inverted (lit bar, dark text), full width
│1 vim  2*serv 3 logs │  legend line 1 → key row 3 (slots 1–3)
│4!rail .      .      │  legend line 2 → key row 4 (slots 4–6)
│ vol 40%        link │  footer: unchanged from v1
└─────────────────────┘
```

- **Header:** `adafruit_display_text` label with `color=0x000000`,
  `background_color=0xFFFFFF`, padded to the display width. Text is
  `"{id} · {label}"` when `ws.names` has the active workspace, else
  `"ws {id}"` (v1 behavior). The `·` (U+00B7) and legend markers `●`/`·` are
  outside terminalio's ASCII range — implementation verifies rendering and
  falls back to ASCII (`-` separator, `*` active, `!` bell, `.` empty) if the
  glyphs don't render. The spec's contract is the layout, not the glyph.
- **Legend:** two lines of three 7-char cells, spatially mapped to key rows
  3–4. Cell = index digit, marker (bell `!` > active `*`/`●` > space), window
  name truncated to 4 chars, trailing space — `"{i}{m}{name:<4.4} "` — so the
  marker hugs its index and cells always stay separated. Empty slot = a lone
  dot in the cell. Shown only in tmux mode; in `mode:"none"` the main area
  falls back to the v1 focused-window title (marquee).

### 6.2 LEDs (`firmware/apps/cockpit.py`, `shared/km_palette.py`)

- Keys 0–5: v1 logic untouched (`ws_key_color` with per-workspace colors).
- Keys 6–11: new `km_palette.ctx_key_color(item, phase)`:
  - slot has a window, active → its index bin at full brightness
  - slot has a window, inactive → index bin scaled by `OCCUPIED_SCALE`
  - slot has a window, bell → urgent blink (same red triangle-wave as v1
    urgent workspaces; bell wins over active/inactive)
  - empty slot, or `mode:"none"` → off
- `km_palette.INDEX_BINS` = the six canonical Okabe-Ito hues in canonical
  order (window index 1 → bin 1, etc.):
  `E69F00` orange, `56B4E9` sky blue, `009E73` bluish green, `F0E442` yellow,
  `0072B2` blue, `D55E00` vermillion. Canonical hues (not the WSID theme
  palettes) because NeoPixels have no background to contrast against and
  saturated hues render truest; the WSID palettes are hue-locked to these, so
  pad and terminal always agree on color *family* (see §8).

## 7. The bell pipeline

### 7.1 The chain

```
Claude (any pane) prints BEL
  → tmux: sets window_bell_flag on that window   [already configured]
          + passes BEL through to its client      [already configured]
  → foot: raises XDG urgency                      [NEW: foot.ini [bell] urgent=yes]
  → Hyprland: window → urgent, urgentwindow event [built in]
  → keymakerd: urgent workspace in ws msg         [shipped in v1]
  → pad: top-half key blinks                      [shipped in v1]
```

Bottom half needs no pipeline at all: `window_bell_flag` arrives via the
ContextWatcher poll and blinks the window's key.

**The scoping property (why no routing logic exists anywhere):** foot only
raises urgency when its window is unfocused, and tmux only flags inactive
windows. A bell from another workspace blinks the top half; a bell from
another window of the session you're looking at blinks only the bottom half.
Each layer reports exactly its own scope by its own existing rules.

### 7.2 Host config

- `~/.config/foot/foot.ini` gains:

  ```ini
  [bell]
  urgent=yes
  ```

  foot.ini is deliberately chezmoi-untracked → local edit, recorded in oracle
  `docs/setup.md`. Running footguard windows keep the old config until their
  foot respawns; new windows pick it up immediately.

- Claude Code is pointed at its terminal-bell notification channel so a
  waiting Claude rings BEL. The exact setting name (config key vs a
  Notification hook printing `\a`) is verified at implementation.

### 7.3 Verification-first

The riskiest assumption in v2 is foot's urgency → Hyprland urgent mapping.
The plan's first task proves it live: add the stanza, open a fresh foot
window, `printf '\a'` into it from another workspace, and watch
`.socket2.sock` for the urgent event. If Hyprland 0.56 disappoints, the
fallback is foot's `[bell] command` (run an arbitrary command on bell) poking
the daemon directly — the rest of the design is unchanged either way.

### 7.4 Remote bells (rika over mosh) — works by construction

BEL is in-band data, so it crosses hops that side-channel notifications
cannot: Claude on rika → rika's tmux (stock defaults pass it through) →
mosh-server → mosh models the bell as terminal state and re-emits on nexus →
the local tmux window hosting the mosh session gets flagged + passes through
→ foot → pad. Zero code, zero rika changes; the blinking bottom-half key is
the local window running mosh, which is the right granularity — the pad
routes you to the doorway, rika's own status bar takes it from there.

Caveat: mosh coalesces state, so rapid bells may arrive as one. Irrelevant
for "Claude needs you." Remote window *lists* and tap-to-switch do not reach
across (that would be SSH polling of Sysop's box) — out of scope.

## 8. Two-layer colors

- **Layer 1 — workspace identity (exists):** color = `cksum(label) % 7` into
  the WSID palette; already rendered by the top-half keys, the workspace name
  spans, and (until Omarchy 4) waybar.
- **Layer 2 — window index (new):** fixed per-index bins. Window 1 is always
  the bin-1 hue everywhere it appears: pad key 6, and the tmux status bar's
  window entry.

`~/.config/tmux/statusbar.conf` changes:

- Session-name segment tinted with the layer-1 color: a `#()` helper script
  calls `wsid_color` with `#{session_name}` (theme-aware for free, since
  `wsid_color` checks `light.mode`).
- Window entries colored by index from the WSID palette (theme-appropriate
  variant), via the same helper. The WSID palettes are used here — not the
  canonical hues the pad uses — because terminal text must clear contrast on
  theme backgrounds, which is exactly what those palettes were optimized for.
  Hue-locking guarantees pad and bar read as the same color family.
- The existing `window-status-bell-style "fg=red,bold"` already provides the
  terminal-side bell highlight; it stays.

Two disciplines this consciously touches:

- **statusbar.conf's ANSI-only principle** is amended with one sanctioned
  exception: WSID hexes, which are contrast-engineered per theme family and
  already live inside workspace names. The file's header comment is updated
  to say so.
- **statusbar.conf is shared with thinkpad** (chezmoi). The helper is
  referenced by path and the format degrades to the current styling when the
  script is absent — the same degradation discipline footguard uses for the
  wsid lib. Chezmoi state is checked and reconciled per the oracle handbook.

## 9. Error handling and degradation

| Failure | Behavior |
|---|---|
| tmux server down / session killed mid-poll | `ctx mode:"none"`; watcher keeps running |
| Bottom-half tap with `mode:"none"` or empty slot | ignored by daemon |
| `select_window` fails (window closed between poll and tap) | logged, ignored; next poll corrects the deck |
| Session has >6 windows | slots 1–6 shown; 7+ silently absent (documented) |
| Workspace unnamed | header falls back to `ws N`; top-half key falls back to theme accent (v1 behavior) |
| foot urgency unsupported by compositor | `[bell] command` fallback (§7.3) |
| v1 firmware ↔ v2 daemon (or reverse) during deploy | unknown fields/messages ignored; worst case is v1 behavior |

## 10. Testing

- **Pure logic, pytest:** `ws_label` stripping, `ctx` state building and
  change-detection, legend cell formatting, `ctx_key_color` states,
  `list_windows` output parsing (runner injected, no real tmux).
- **tmux integration:** a real tmux server on an isolated socket
  (`tmux -L keymaker-test`) — a separate server process that cannot touch the
  daily-driver tmux server (terminal stability rule). Create session +
  windows, ring a bell in one, assert `list_windows` sees flags;
  `select_window` round-trip.
- **Firmware:** review-verified + live smoke (deploy, console clean, legend
  and LEDs eyeballed), as v1.
- **Bell pipeline:** the §7.3 live proof, plus an end-to-end: bell from an
  unfocused workspace → urgent in `ws` msg; bell in an inactive window of the
  focused session → `bell:true` in `ctx` msg.

## 11. Out of scope (v2)

- Firefox (any treatment) — revisit with fresh eyes; send-only is possible
  today via `hyprctl dispatch sendshortcut`, rich state needs a WebExtension.
- Remote tmux control (rika window lists / switching).
- Bottom-half hold gestures; >6 windows; >6 workspaces on-pad.
- Knob changes (stays volume/mute); Coach (separate plan, unchanged).

## 12. Open items front-loaded into the plan

1. Live proof: foot `[bell] urgent=yes` → Hyprland urgent event (§7.3).
2. Exact Claude Code bell setting (config channel vs Notification hook).
3. Glyph check: `·` and `●` in terminalio font; ASCII fallbacks specified.
4. Chezmoi status of `statusbar.conf` and reconciliation path.
