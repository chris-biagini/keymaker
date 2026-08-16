#!/usr/bin/env python3
"""Generate the coach one-shots. Deterministic (seeded RNG), stdlib only.

Outputs are committed to firmware/sounds/ so the device deploy stays a
plain rsync; tests/test_drums.py asserts the committed bytes match this
generator. Tweak a constant, re-run, re-commit."""
import math
import random
import struct
import wave
from pathlib import Path

RATE = 22050
PEAK = int(0.707 * 32767)          # -3 dBFS
OUT = Path(__file__).resolve().parent.parent / "firmware" / "sounds"


def _env(t, tau):
    return math.exp(-t / tau)


def _write(name, samples):
    top = max(1e-9, max(abs(s) for s in samples))
    frames = b"".join(struct.pack("<h", int(s / top * PEAK)) for s in samples)
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / (name + ".wav")), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


def kick():
    # Bench 2026-08-16: the 120->45 Hz sweep was nearly inaudible on the
    # MacroPad's ~20 mm speaker (no low end down there). Higher sweep with a
    # short noise transient reads as "kick" within what the driver can do.
    out, phase = [], 0.0
    for i in range(int(0.160 * RATE)):
        t = i / RATE
        f = 190.0 * (50.0 / 190.0) ** (t / 0.160)   # exponential pitch sweep
        phase += 2.0 * math.pi * f / RATE
        out.append(math.sin(phase) * _env(t, 0.075))
    rng = random.Random(1)                          # deterministic transient
    n = int(0.003 * RATE)
    for i in range(n):
        out[i] += rng.uniform(-0.6, 0.6) * (1.0 - i / n)
    return out


def snare(rng):
    out = []
    for i in range(int(0.250 * RATE)):
        t = i / RATE
        tone = math.sin(2.0 * math.pi * 185.0 * t) * _env(t, 0.080)
        noise = rng.uniform(-1.0, 1.0) * _env(t, 0.120)
        out.append(0.5 * tone + 0.5 * noise)
    return out


def hat(rng):
    out, prev = [], 0.0
    for i in range(int(0.120 * RATE)):
        t = i / RATE
        x = rng.uniform(-1.0, 1.0)
        out.append((x - prev) * 0.5 * _env(t, 0.045))   # first difference ~ highpass
        prev = x
    return out


def click(freq):
    return [math.sin(2.0 * math.pi * freq * i / RATE) * _env(i / RATE, 0.008)
            for i in range(int(0.030 * RATE))]


def main():
    rng = random.Random(0)
    _write("kick", kick())
    _write("snare", snare(rng))
    _write("hat", hat(rng))
    _write("click_hi", click(1500.0))
    _write("click_lo", click(1000.0))
    print("wrote 5 wavs to", OUT)


if __name__ == "__main__":
    main()
