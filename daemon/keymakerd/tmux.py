"""tmux CLI wrapper: window lists and switching. Subprocess-per-call; never raises."""
import asyncio

FORMAT = "#{window_index}\t#{window_active}\t#{window_bell_flag}\t#{window_name}"


async def _run(*args):
    proc = await asyncio.create_subprocess_exec(
        "tmux", *args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


def parse_windows(out):
    items = []
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        idx, active, bell, name = parts
        try:
            i = int(idx)
        except ValueError:
            continue
        items.append({"i": i, "name": name,
                      "active": active == "1", "bell": bell == "1"})
    return items


async def list_windows(session, run=_run):
    """Windows of a session as [{i, name, active, bell}], or None on failure."""
    try:
        rc, out = await run("list-windows", "-t", "=" + session, "-F", FORMAT)
    except OSError:
        return None
    if rc != 0:
        return None
    return parse_windows(out)


async def select_window(session, i, run=_run):
    """Select window i of session (exact-name match). True on success."""
    try:
        rc, _ = await run("select-window", "-t", "=%s:%d" % (session, i))
    except OSError:
        return False
    return rc == 0


# ---- attention ledger sources ----------------------------------------------
# Server-wide reads for the OLED ledger: un-ack'd bells across every session
# (window_bell_flag clears when the window is visited -- tmux's own semantics
# ARE the ack), and Claude Code panes, whose OSC-set titles carry a state glyph
# plus a live task summary. Nobody built that as an API; it falls out of title
# setting plus tmux bookkeeping, same family as the BEL pipeline.

BELLS_FORMAT = "#{session_name}\t#{window_index}\t#{window_bell_flag}\t#{window_name}"
PANES_FORMAT = "#{session_name}\t#{window_index}\t#{pane_current_command}\t#{pane_title}"

# Claude Code's idle/attention title glyph. While working the glyph animates
# through spinner frames that vary by version, so busy is defined as "any OTHER
# leading non-ASCII glyph" rather than an allowlist of frames.
_IDLE_GLYPH = "✳"          # U+2733
_TITLE_MAX = 40
_SESSION_MAX = 20


def _ascii(text):
    """terminalio's font is ASCII; strip anything it cannot draw."""
    return "".join(ch for ch in text if 32 <= ord(ch) < 127)


def parse_bells(out):
    """Windows with an un-ack'd bell, as [{s, i, name}]."""
    items = []
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        s, idx, bell, _name = parts
        try:
            i = int(idx)
        except ValueError:
            continue
        if bell == "1":
            items.append({"s": _ascii(s)[:_SESSION_MAX], "i": i})
    return items


def parse_claude_panes(out):
    """Claude Code panes as [{s, i, busy, title}], glyph stripped from title."""
    items = []
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        s, idx, cmd, title = parts
        if cmd != "claude":
            continue
        try:
            i = int(idx)
        except ValueError:
            continue
        # A glyphless title means Claude Code never OSC-set it -- tmux's default
        # pane_title is the HOSTNAME, which would render as a bogus "waiting"
        # task. No glyph, no entry; a bell still covers a title-less Claude.
        if not title or ord(title[0]) <= 127:
            continue
        busy = title[0] != _IDLE_GLYPH
        items.append({"s": _ascii(s)[:_SESSION_MAX], "i": i, "busy": busy,
                      "title": _ascii(title[1:]).strip()[:_TITLE_MAX]})
    return items


async def list_bells(run=_run):
    """Un-ack'd bell windows across ALL sessions, or None on failure."""
    try:
        rc, out = await run("list-windows", "-a", "-F", BELLS_FORMAT)
    except OSError:
        return None
    if rc != 0:
        return None
    return parse_bells(out)


async def list_claude_panes(run=_run):
    """Claude Code panes across ALL sessions, or None on failure."""
    try:
        rc, out = await run("list-panes", "-a", "-F", PANES_FORMAT)
    except OSError:
        return None
    if rc != 0:
        return None
    return parse_claude_panes(out)
