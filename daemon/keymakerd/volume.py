"""PipeWire volume via wpctl subprocesses."""
import asyncio

SINK = "@DEFAULT_AUDIO_SINK@"


def parse_volume(out):
    parts = out.split()
    if len(parts) < 2 or parts[0] != "Volume:":
        raise ValueError(f"unexpected wpctl output: {out!r}")
    return float(parts[1]), out.rstrip().endswith("[MUTED]")


async def _run(*args):
    proc = await asyncio.create_subprocess_exec(
        "wpctl", *args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return out.decode()


async def step(direction):
    suffix = "5%+" if direction > 0 else "5%-"
    await _run("set-volume", "-l", "1.0", SINK, suffix)


async def toggle_mute():
    await _run("set-mute", SINK, "toggle")


async def status():
    return parse_volume(await _run("get-volume", SINK))
