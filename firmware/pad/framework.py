"""Main loop: input polling, link polling, app switching via encoder long-press."""
from adafruit_ticks import ticks_diff, ticks_ms

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
    enc_tracker = KeyTracker(hold_ms=HOLD_MENU_MS, diff=ticks_diff)
    last_pos = macropad.encoder
    link.send({"t": "hello", "fw": "0.1.0", "app": apps[active].name})
    apps[active].on_show()

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
                    macropad.pixels.fill(0)
                    apps[active].on_show()
                else:
                    apps[active].on_click()
        if enc_tracker.tick(now):               # long press → open menu
            menu_idx = active

        pos = macropad.encoder
        delta, last_pos = pos - last_pos, pos
        if delta:
            if menu_idx is not None:
                menu_idx = (menu_idx + delta) % len(apps)
            else:
                apps[active].on_dial(delta)

        event = macropad.keys.events.get()
        while event is not None:
            if menu_idx is None:
                apps[active].on_key_event(event.key_number, event.pressed, now)
            event = macropad.keys.events.get()

        for m in link.poll(now):
            apps[active].on_msg(m)

        if menu_idx is not None:
            screen.header.text = "apps"
            screen.title.text = "> " + apps[menu_idx].name
            screen.footer.text = "click to switch"
        else:
            apps[active].tick(now)
