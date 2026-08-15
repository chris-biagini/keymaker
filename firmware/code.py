import supervisor

from adafruit_macropad import MacroPad

from pad.framework import App, run


class Blink(App):
    name = "blink"

    def on_show(self):
        self.screen.idle_card()

    def on_key_event(self, n, pressed, now):
        self.pad.pixels[n] = 0x203040 if pressed else 0

    def on_msg(self, msg):
        self.screen.footer.text = msg.get("t", "?")


macropad = MacroPad()
supervisor.runtime.autoreload = True
run(macropad, [Blink()])
