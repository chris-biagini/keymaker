"""Coach: finger-drumming trainer. Keys 0-5 stages, 9/10/11 kick/snare/hat."""
from adafruit_ticks import ticks_add, ticks_diff, ticks_ms

import km_coach
import km_palette
from pad.framework import App

try:
    import usb_midi
    import adafruit_midi
    from adafruit_midi.note_off import NoteOff
    from adafruit_midi.note_on import NoteOn
except ImportError:
    usb_midi = None

DRUM_KEYS = {9: km_coach.KICK, 10: km_coach.SNARE, 11: km_coach.HAT}
KEY_OF = {km_coach.KICK: 9, km_coach.SNARE: 10, km_coach.HAT: 11}
MIDI_NOTES = {km_coach.KICK: 36, km_coach.SNARE: 38, km_coach.HAT: 42}
VERDICT = {"green": km_coach.COL_GREEN, "amber": km_coach.COL_AMBER,
           "red": km_coach.COL_RED, "stray": km_coach.COL_RED,
           "miss": km_coach.COL_RED}
DIM = 0x141414          # resting drum keys
PULSE = 0x202020        # non-accent beat flash
NEUTRAL = 0x404040      # unscored hit feedback
LOCKED_SCALE = 0.12
FLASH_MS = 150
BEAT_MS = 120
RESULTS_MS = 6000
HORIZON_MS = 200.0      # emit loops this early so early hits find their slots


class Coach(App):
    name = "coach"

    def __init__(self):
        self.bpm = km_coach.BPM_DEFAULT
        self.swing = 50
        self.stage = 1
        self.hstate = {}            # last coach state msg from host
        self.local = []             # sessions completed this power-cycle
        self.queue = []             # sessions not yet sent
        self.mode = "idle"          # idle|countin|playing|results|grad
        self.scorer = None
        self.audio = None
        self.midi = None
        self._flash = {}            # key -> (color, until_ticks)
        self._beat_until = None
        self._beat_accent = False
        self._px = [None] * 12
        self._results_at = 0
        self._res = None
        self._note = ""

    # ---- lifecycle -------------------------------------------------
    def on_show(self):
        if self.audio is None:
            from pad.audio import CoachAudio
            self.audio = CoachAudio(self.pad)
        if self.midi is None and usb_midi is not None:
            try:
                self.midi = adafruit_midi.MIDI(midi_out=usb_midi.ports[1],
                                               out_channel=9)
            except Exception as e:
                print("midi init failed:", repr(e))
        self.audio.enable()
        self.mode = "idle"
        self._flash = {}
        self._px = [None] * 12
        self._draw_idle()

    def on_hide(self):
        self.mode = "idle"
        self.scorer = None
        if self.audio is not None:
            self.audio.disable()

    def on_msg(self, msg):
        if msg.get("t") == "coach":
            self.hstate = msg
            if self.mode == "idle":
                self._draw_idle()

    # ---- input -----------------------------------------------------
    def on_dial(self, delta):
        if self.mode == "idle":
            self.bpm = min(km_coach.BPM_MAX, max(
                km_coach.BPM_MIN, self.bpm + delta * km_coach.BPM_STEP))
            self._draw_idle()
        elif self.mode == "playing" and km_coach.STAGES[self.stage]["swing"]:
            self.swing = min(km_coach.SWING_MAX, max(
                km_coach.SWING_MIN, self.swing + delta))
            self._draw_play()

    def on_click(self):
        if self.mode in ("countin", "playing"):
            self.mode = "idle"
            self.scorer = None
            self._draw_idle()

    def on_key_event(self, n, pressed, now):
        if not pressed:
            return
        if self.mode in ("results", "grad"):
            self.mode = "idle"
            self._draw_idle()
            return
        if n in DRUM_KEYS:
            self._hit(DRUM_KEYS[n], n, now)
        elif n <= 5 and self.mode == "idle":
            self._select(n, now)

    # ---- session ---------------------------------------------------
    def _unlocked(self):
        return km_coach.merge_unlock(self.hstate, self.local)

    def _select(self, s, now):
        unlocked, _ = self._unlocked()
        if s > unlocked:
            self._flash[s] = (km_coach.COL_RED, ticks_add(now, FLASH_MS))
            return
        self.stage = s
        self.epoch = now
        q = 60000.0 / self.bpm
        self.quarter = q
        if s == 0:                              # endless metronome
            self.mode = "playing"
            self.scorer = None
            self._next_click = 0.0
            self._beat_n = 0
            self._draw_play()
            return
        self.mode = "countin"
        self.play_start = 4.0 * q
        self.loop_ms = 8.0 * q                  # two bars
        self.next_loop = 0
        self.clicks = [(i * q, i == 0) for i in range(4)]
        self.scorer = km_coach.SessionScorer(
            variance=km_coach.STAGES[s]["variance"])
        self._bar_shown = 0
        self._draw_play()

    def _emit_loop(self, l):
        base = self.play_start + l * self.loop_ms
        for b in range(8):
            self.clicks.append((base + b * self.quarter, b % 4 == 0))
        for instr, off in km_coach.loop_grid_ms(self.stage, self.bpm,
                                                self.swing):
            self.scorer.add_expected(instr, base + off)

    def _hit(self, instr, key, now):
        self.audio.play(instr)
        self._midi(instr)
        if self.mode == "playing" and self.scorer is not None:
            t = float(ticks_diff(now, self.epoch))
            v = self.scorer.on_hit(instr, t)
            self._flash[key] = (VERDICT[v], ticks_add(now, FLASH_MS))
            self._update_acc()
        else:
            self._flash[key] = (NEUTRAL, ticks_add(now, FLASH_MS))

    def _midi(self, instr):
        if self.midi is None:
            return
        try:
            note = MIDI_NOTES[instr]
            self.midi.send(NoteOn(note, 100))
            self.midi.send(NoteOff(note, 0))
        except Exception as e:
            print("midi send failed:", repr(e))

    def _finish(self, now):
        res = self.scorer.finalize()
        st = km_coach.STAGES[self.stage]
        sess = {"stage": self.stage, "bpm": self.bpm,
                "swing": self.swing if st["swing"] else None,
                "greens": res["greens"], "ambers": res["ambers"],
                "reds": res["reds"], "misses": res["misses"],
                "strays": res["strays"], "score": res["score"],
                "duration_ms": ticks_diff(now, self.epoch)}
        u_before, g_before = self._unlocked()
        self.local.append(sess)
        self.queue.append(sess)
        u_after, g_after = self._unlocked()
        self.scorer = None
        self._res = res
        self._results_at = now
        if g_after and not g_before:
            self.mode = "grad"
            self._draw_grad()
            return
        if u_after > u_before:
            self._note = "unlocked: " + km_coach.STAGES[u_after]["name"]
        elif st["variance"] and res.get("mean_offset", 0.0) < km_coach.LATE_GATE_MS:
            self._note = "drag it"
        else:
            self._note = "need 85% x3"
        self.mode = "results"
        self._draw_results()

    # ---- tick ------------------------------------------------------
    def tick(self, now):
        if self.mode in ("countin", "playing") and self.stage == 0:
            t = float(ticks_diff(now, self.epoch))
            while t >= self._next_click:
                accent = self._beat_n % 4 == 0
                self.audio.play("click_hi" if accent else "click_lo")
                self._beat_until = ticks_add(now, BEAT_MS)
                self._beat_accent = accent
                self._next_click += self.quarter
                self._beat_n += 1
        elif self.mode in ("countin", "playing"):
            t = float(ticks_diff(now, self.epoch))
            if self.mode == "countin" and t >= self.play_start:
                self.mode = "playing"
            while self.clicks and t >= self.clicks[0][0]:
                _, accent = self.clicks.pop(0)
                self.audio.play("click_hi" if accent else "click_lo")
                self._beat_until = ticks_add(now, BEAT_MS)
                self._beat_accent = accent
            if (self.next_loop < km_coach.LOOPS
                    and t >= self.play_start + self.next_loop * self.loop_ms
                    - HORIZON_MS):
                self._emit_loop(self.next_loop)
                self.next_loop += 1
            for instr, _et in self.scorer.expire(t):
                self._flash[KEY_OF[instr]] = (VERDICT["miss"],
                                              ticks_add(now, FLASH_MS))
                self._update_acc()
            bar = 1 + int(max(0.0, t - self.play_start) / (self.loop_ms / 2.0))
            if self.mode == "playing" and bar != self._bar_shown:
                self._bar_shown = min(bar, 16)
                self._draw_play()
            if t >= self.play_start + km_coach.LOOPS * self.loop_ms + km_coach.MISS_MS:
                self._finish(now)
        elif self.mode == "results" and ticks_diff(now, self._results_at) >= RESULTS_MS:
            self.mode = "idle"
            self._draw_idle()
        if self.queue and self.link.up:
            for sess in self.queue:
                self.link.send({"t": "coach", "session": sess})
            self.queue = []
        self._leds(now)

    # ---- output ----------------------------------------------------
    def _set_px(self, i, c):
        if self._px[i] != c:
            self.pad.pixels[i] = c
            self._px[i] = c

    def _leds(self, now):
        if self.mode in ("idle", "results", "grad"):
            unlocked, _ = self._unlocked()
            for s in range(6):
                c = km_palette.hex_to_int(km_palette.INDEX_BINS[s])
                self._set_px(s, c if s <= unlocked
                             else km_palette.scale(c, LOCKED_SCALE))
            for n in (6, 7, 8):
                self._set_px(n, 0)
            for n in (9, 10, 11):
                self._set_px(n, DIM)
        else:
            beat = (self._beat_until is not None
                    and ticks_diff(self._beat_until, now) > 0)
            row0 = (km_coach.COL_ACCENT if self._beat_accent else PULSE) \
                if beat else 0
            for n in (0, 1, 2):
                self._set_px(n, row0)
            for n in (3, 4, 5, 6, 7, 8):
                self._set_px(n, 0)
            for n in (9, 10, 11):
                self._set_px(n, DIM)
        for key in list(self._flash):
            color, until = self._flash[key]
            if ticks_diff(until, now) > 0:
                self._set_px(key, color)
            else:
                del self._flash[key]
                self._px[key] = None            # force repaint next frame

    def _best(self):
        info = (self.hstate.get("stages") or {}).get(str(self.stage))
        if not info:
            return ""
        return "  best " + str(int(info["best"] * 100.0 + 0.5)) + "%"

    def _draw_idle(self):
        st = km_coach.STAGES[self.stage]
        self.screen.set_header("coach")
        self.screen.line1.text = "stage " + str(self.stage) + " " + st["name"]
        self.screen.line2.text = "bpm " + str(self.bpm) + self._best()
        self.screen.footer.text = "tap a stage  knob bpm"

    def _draw_play(self):
        st = km_coach.STAGES[self.stage]
        self.screen.set_header(str(self.stage) + " " + st["name"])
        if self.stage == 0:
            self.screen.line1.text = "bpm " + str(self.bpm)
            self.screen.line2.text = ""
        elif self.mode == "countin":
            self.screen.line1.text = "bpm " + str(self.bpm) + "  count-in"
            self.screen.line2.text = ""
        else:
            lead = ("swing " + str(self.swing) + "%") if st["swing"] \
                else ("bpm " + str(self.bpm))
            self.screen.line1.text = lead + "  bar " + \
                str(self._bar_shown) + "/16"
            self._update_acc()
        self.screen.footer.text = "click = stop"

    def _update_acc(self):
        if self.scorer is None:
            return
        la = self.scorer.live_accuracy()
        self.screen.line2.text = "acc --" if la is None \
            else "acc " + str(int(la * 100.0 + 0.5)) + "%"

    def _draw_results(self):
        st = km_coach.STAGES[self.stage]
        self.screen.set_header("results")
        self.screen.line1.text = st["name"] + "  score " + \
            str(int(self._res["score"] * 100.0 + 0.5)) + "%"
        self.screen.line2.text = km_coach.format_results(self._res)
        self.screen.footer.text = self._note

    def _draw_grad(self):
        self.screen.set_header("graduated")
        self.screen.line1.text = "you have consistency"
        self.screen.line2.text = "you need velocity"
        self.screen.footer.text = "the akai awaits"
