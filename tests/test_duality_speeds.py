#!/usr/bin/env python3
import os
import time

LED_DIR = "/sys/class/leds/go_s:rgb:joystick_rings"

def write_sysfs(attr, val):
    try:
        with open(os.path.join(LED_DIR, attr), 'w') as f:
            f.write(str(val))
    except PermissionError:
        print(f"Permission denied writing to {attr}. Did you run with sudo?")
        exit(1)

print("--- ROG Ally X: Duality Speed Test ---")
print("Press ENTER to proceed through each step.\n")

# Initial setup
write_sysfs("effect", "breathe")
write_sysfs("multi_intensity", "0 255 0") # Primary: Green
write_sysfs("color2", "255 0 255")        # Background: Purple

input("[Press ENTER] Step 1: Slow Speed (Green <-> Purple)")
write_sysfs("speed", "0")
print("-> Expected: Very slow breathing cycle (~13s).\n")

input("[Press ENTER] Step 2: Medium Speed (Green <-> Purple)")
write_sysfs("speed", "50")
print("-> Expected: Medium breathing cycle (~9s).\n")

input("[Press ENTER] Step 3: Fast Speed (Green <-> Purple)")
write_sysfs("speed", "100")
print("-> Expected: Fast breathing cycle (~5s).\n")

input("[Press ENTER] to finish testing and clean up")
print("Cleaning up...")
write_sysfs("color2", "0 0 0")
write_sysfs("speed", "50") # Restore to default medium
print("Test complete!")
