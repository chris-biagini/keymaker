"""Main loop: input polling, link polling, app switching via encoder long-press."""
from adafruit_ticks import ticks_add, ticks_diff, ticks_ms

from km_keys import KeyTracker

from .link import Link
from .ui import Screen

HOLD_MENU_MS = 600
BRIGHTNESS = 0.3


class App:
    name = "app"

    def attach(self, pad, link, screen):
        self.pad = pad
        self.link = link
        self.screen = screen

    def on_show(self):
        pass

    def on_hide(self):
        pass

    def on_key_event(self, n, pressed, now):
        pass

    def on_dial(self, delta):
        pass

    def on_click(self):
        pass

    def on_msg(self, msg):
        pass

    def tick(self, now):
        pass


def run(macropad, apps):
    link = Link(ticks_ms, ticks_diff)
    screen = Screen(macropad.display)
    macropad.pixels.brightness = BRIGHTNESS
    for app in apps:
        app.attach(macropad, link, screen)

    active = 0
    menu_idx = None            # not None → menu is open
    ledtest_until = None       # ticks_ms deadline for a debug LED frame; None = inactive
    enc_tracker = KeyTracker(hold_ms=HOLD_MENU_MS, diff=ticks_diff)
    last_pos = macropad.encoder
    link.send({"t": "hello", "fw": "0.1.0", "app": apps[active].name})
    try:
        apps[active].on_show()
    except Exception as e:
        print("on_show error:", repr(e))

    while True:
        now = ticks_ms()

        macropad.encoder_switch_debounced.update()
        if macropad.encoder_switch_debounced.pressed:
            enc_tracker.press("enc", now)
        if macropad.encoder_switch_debounced.released:
            if enc_tracker.release("enc", now) == "tap":
                if menu_idx is not None:
                    active, menu_idx = menu_idx, None
                    link.send({"t": "hello", "fw": "0.1.0", "app": apps[active].name})
                    ledtest_until = None       # a switch outranks any debug frame
                    macropad.pixels.auto_write = True
                    macropad.pixels.fill(0)
                    try:
                        apps[active].on_show()
                    except Exception as e:
                        print("on_show error:", repr(e))
                else:
                    try:
                        apps[active].on_click()
                    except Exception as e:
                        print("on_click error:", repr(e))
        if enc_tracker.tick(now):               # long press → open menu
            menu_idx = active
            try:
                apps[active].on_hide()
            except Exception as e:
                print("on_hide error:", repr(e))

        pos = macropad.encoder
        delta, last_pos = pos - last_pos, pos
        if delta:
            if menu_idx is not None:
                menu_idx = (menu_idx + delta) % len(apps)
            else:
                try:
                    apps[active].on_dial(delta)
                except Exception as e:
                    print("on_dial error:", repr(e))

        event = macropad.keys.events.get()
        while event is not None:
            if menu_idx is None:
                try:
                    apps[active].on_key_event(event.key_number, event.pressed,
                                              event.timestamp)
                except Exception as e:
                    print("on_key_event error:", repr(e))
            event = macropad.keys.events.get()

        for m in link.poll(now):
            # Debug LED frame (host-side palette eyeballing). There is no
            # framework "LED pass" to skip -- apps repaint all 12 pixels every
            # tick -- but no app ever calls pixels.show(), so gating auto_write
            # freezes the visible frame while apps keep their state fresh via
            # on_msg/tick. rgb is already post-gamma PWM: the daemon linearizes.
            if m.get("t") == "ledtest":
                try:
                    macropad.pixels.auto_write = False
                    for i, rgb in enumerate(m.get("rgb", [])[:12]):
                        macropad.pixels[i] = tuple(rgb)
                    macropad.pixels.show()
                    ledtest_until = ticks_add(now, int(m.get("hold", 30)) * 1000)
                except Exception as e:
                    # A raise here with auto_write already off would strand the
                    # LEDs frozen forever -- restore before giving up.
                    print("ledtest error:", repr(e))
                    ledtest_until = None
                    macropad.pixels.auto_write = True
                continue                       # never reaches the app
            try:
                apps[active].on_msg(m)
            except Exception as e:
                print("on_msg error:", repr(e))

        if ledtest_until is not None and ticks_diff(now, ledtest_until) >= 0:
            ledtest_until = None
            macropad.pixels.auto_write = True
            if menu_idx is None:               # the menu paints no pixels
                try:
                    apps[active].on_show()     # repaint current state
                except Exception as e:
                    print("on_show error:", repr(e))

        if menu_idx is not None:
            screen.set_header("apps")
            screen.line1.text = "> " + apps[menu_idx].name
            screen.line2.text = ""
            screen.footer.text = "click to switch"
        else:
            try:
                apps[active].tick(now)
            except Exception as e:
                print("tick error:", repr(e))
