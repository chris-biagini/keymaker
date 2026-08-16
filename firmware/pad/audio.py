"""Speaker one-shots: 4-voice mixer so click/kick/snare/hat overlap freely.

Degrades to no-ops if audio libs or hardware are unavailable — the trainer
still works silently (LEDs, OLED, MIDI, scoring)."""
try:
    import audiocore
    import audiomixer
    import audiopwmio
    import board
    from adafruit_ticks import ticks_diff, ticks_ms
except ImportError:
    audiopwmio = None

_NAMES = ("click_hi", "click_lo", "kick", "snare", "hat")
_VOICE = {"click_hi": 0, "click_lo": 0, "kick": 1, "snare": 2, "hat": 3}

# The PWM output runs continuously; with the amp enabled that carrier is an
# audible idle hiss. Gate SPEAKER_ENABLE around activity instead: on at
# play(), off after this much silence. Must exceed the widest click gap
# (1000 ms at 60 BPM) so a session never gates mid-groove.
IDLE_MS = 1500


class CoachAudio:
    def __init__(self, macropad, root="/sounds"):
        # MacroPad() already claims SPEAKER_ENABLE; reuse its pin object.
        self._enable = getattr(macropad, "_speaker_enable", None)
        self._mixer = None
        self._wavs = {}
        self._last_play = None
        if audiopwmio is None:
            return
        try:
            self._out = audiopwmio.PWMAudioOut(board.SPEAKER)
            self._mixer = audiomixer.Mixer(voice_count=4, sample_rate=22050,
                                           channel_count=1, bits_per_sample=16,
                                           samples_signed=True)
            self._out.play(self._mixer)
            for name in _NAMES:
                self._wavs[name] = audiocore.WaveFile(
                    open(root + "/" + name + ".wav", "rb"))
        except Exception as e:
            print("audio init failed:", repr(e))
            self._mixer = None

    def enable(self):
        if self._enable is not None:
            self._enable.value = True

    def disable(self):
        if self._enable is not None:
            self._enable.value = False

    def play(self, name):
        if self._mixer is None or name not in self._wavs:
            return
        try:
            self.enable()
            self._last_play = ticks_ms()
            self._mixer.voice[_VOICE[name]].play(self._wavs[name])
        except Exception as e:
            print("audio play failed:", repr(e))

    def tick(self, now):
        if self._mixer is None or self._last_play is None:
            return
        if ticks_diff(now, self._last_play) > IDLE_MS:
            self.disable()
            self._last_play = None
