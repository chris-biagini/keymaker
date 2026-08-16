import supervisor

from adafruit_macropad import MacroPad

from apps.cockpit import Cockpit
from apps.coach import Coach
from pad.framework import run

macropad = MacroPad()
supervisor.runtime.autoreload = True
run(macropad, [Cockpit(), Coach()])
