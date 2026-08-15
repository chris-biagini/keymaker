import time
import usb_cdc

print("keymaker skeleton", "data channel:", usb_cdc.data is not None)
while True:
    time.sleep(1)
