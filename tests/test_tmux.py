import asyncio
import shutil
import subprocess
import uuid

import pytest

from keymakerd import tmux


def test_parse_windows_happy_path():
    out = "1\t0\t0\tmain\n2\t1\t0\tserver\n3\t0\t1\tlogs\n"
    assert tmux.parse_windows(out) == [
        {"i": 1, "name": "main", "active": False, "bell": False},
        {"i": 2, "name": "server", "active": True, "bell": False},
        {"i": 3, "name": "logs", "active": False, "bell": True},
    ]


def test_parse_windows_skips_malformed_lines():
    out = "garbage\nx\t1\t0\tname\n1\t1\t0\tok\n\n"
    assert tmux.parse_windows(out) == [
        {"i": 1, "name": "ok", "active": True, "bell": False},
    ]


def test_parse_windows_keeps_tabs_in_names():
    # split("\t", 3): a name containing a tab survives intact
    assert tmux.parse_windows("1\t0\t0\ta\tb\n")[0]["name"] == "a\tb"


def test_list_windows_none_on_failure():
    async def bad_rc(*args):
        return 1, ""

    async def raises(*args):
        raise OSError("no tmux binary")

    assert asyncio.run(tmux.list_windows("s", run=bad_rc)) is None
    assert asyncio.run(tmux.list_windows("s", run=raises)) is None


def test_list_and_select_use_exact_session_match():
    calls = []

    async def fake(*args):
        calls.append(args)
        return 0, ""

    asyncio.run(tmux.list_windows("mac", run=fake))
    asyncio.run(tmux.select_window("mac", 3, run=fake))
    assert calls[0] == ("list-windows", "-t", "=mac", "-F", tmux.FORMAT)
    assert calls[1] == ("select-window", "-t", "=mac:3")


def test_select_window_false_on_failure():
    async def bad_rc(*args):
        return 1, ""

    async def raises(*args):
        raise OSError("gone")

    assert asyncio.run(tmux.select_window("s", 1, run=bad_rc)) is False
    assert asyncio.run(tmux.select_window("s", 1, run=raises)) is False


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_against_real_tmux_isolated_server(tmp_path):
    """Real tmux, isolated socket + own conf: the daily server is untouched."""
    conf = tmp_path / "tmux.conf"
    conf.write_text("set -g base-index 1\nset -g renumber-windows on\n")
    sock = f"km-test-{uuid.uuid4().hex[:8]}"
    tm = ["tmux", "-L", sock, "-f", str(conf)]
    subprocess.run(tm + ["new-session", "-d", "-s", "alpha", "-x", "80", "-y", "24"],
                   check=True)
    try:
        subprocess.run(tm + ["new-window", "-t", "=alpha", "-n", "second"], check=True)

        async def run(*args):
            proc = await asyncio.create_subprocess_exec(
                *tm, *args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            return proc.returncode, out.decode()

        async def scenario():
            wins = await tmux.list_windows("alpha", run=run)
            ok = await tmux.select_window("alpha", 1, run=run)
            wins2 = await tmux.list_windows("alpha", run=run)
            missing = await tmux.list_windows("no-such-session", run=run)
            return wins, ok, wins2, missing

        wins, ok, wins2, missing = asyncio.run(scenario())
        assert [w["i"] for w in wins] == [1, 2]
        assert wins[1]["active"] is True          # new-window made it active
        assert ok is True
        assert wins2[0]["active"] is True         # select-window took effect
        assert missing is None
    finally:
        subprocess.run(tm + ["kill-server"], check=False)
