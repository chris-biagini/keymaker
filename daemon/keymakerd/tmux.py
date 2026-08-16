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
