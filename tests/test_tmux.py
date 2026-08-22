import asyncio
import shutil
import subprocess
import uuid

import pytest

from keymakerd import tmux


def test_select_uses_exact_session_match():
    calls = []

    async def fake(*args):
        calls.append(args)
        return 0, ""

    asyncio.run(tmux.select_window("mac", 3, run=fake))
    assert calls[0] == ("select-window", "-t", "=mac:3")


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
            wins = await tmux.list_deck_windows(run=run)
            wins = [w for w in wins if w["s"] == "alpha"]
            ok = await tmux.select_window("alpha", 1, run=run)
            wins2 = await tmux.list_deck_windows(run=run)
            wins2 = [w for w in wins2 if w["s"] == "alpha"]
            return wins, ok, wins2

        wins, ok, wins2 = asyncio.run(scenario())
        assert [w["i"] for w in wins] == [1, 2]
        assert wins[1]["active"] is True          # new-window made it active
        assert ok is True
        assert wins2[0]["active"] is True         # select-window took effect
    finally:
        subprocess.run(tm + ["kill-server"], check=False)


def test_parse_deck_windows_uses_tmux_window_id_not_session_index():
    # Field order (see tmux.DECK_FORMAT for the full note): session,
    # window_id, index, active, bell, name -- active before bell. Rows 1-2
    # deliberately have active/bell asymmetric so a swap between the two
    # would fail this test; the third row is all-zero and can't catch it.
    out = ("mirepoix\t@2\t1\t1\t0\trails\n"
           "mirepoix\t@5\t3\t0\t1\tlogs\n"
           "colorhash\t@9\t1\t0\t0\tlab\n")
    got = tmux.parse_deck_windows(out)
    assert got == [
        {"id": "tmux:@2", "s": "mirepoix", "i": 1, "n": "rails",
         "active": True, "bell": False},
        {"id": "tmux:@5", "s": "mirepoix", "i": 3, "n": "logs",
         "active": False, "bell": True},
        {"id": "tmux:@9", "s": "colorhash", "i": 1, "n": "lab",
         "active": False, "bell": False},
    ]


def test_parse_deck_windows_skips_malformed_and_unparseable_index():
    out = "sess\t@1\tnotanint\t0\t0\tname\nsess\t@2\ttoo\tfew\n\nok\t@3\t2\t0\t0\tn\n"
    assert [x["id"] for x in tmux.parse_deck_windows(out)] == ["tmux:@3"]


def test_deck_format_requests_window_id():
    assert "#{window_id}" in tmux.DECK_FORMAT
    assert "#{window_bell_flag}" in tmux.DECK_FORMAT
