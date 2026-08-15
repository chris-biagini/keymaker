import usb_cdc

# Console (REPL) on the first CDC interface, data channel on the second.
# Takes effect on hard reset only (power cycle / reset button), not auto-reload.
usb_cdc.enable(console=True, data=True)
