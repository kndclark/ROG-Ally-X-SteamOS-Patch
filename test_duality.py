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

print("--- ROG Ally X: Duality (Phase 3) Test ---")
print("Press ENTER to proceed through each step.\n")

input("[Press ENTER] Step 1: Set primary color to Red and effect to Breathe")
write_sysfs("multi_intensity", "255 0 0")
write_sysfs("effect", "breathe")
print("-> Expected: Red fading to Black.\n")

input("[Press ENTER] Step 2: Set secondary background color to Blue")
write_sysfs("color2", "0 0 255")
print("-> Expected: Smooth crossfade between Red and Blue.\n")

input("[Press ENTER] Step 3: Clear secondary background color (Black)")
write_sysfs("color2", "0 0 0")
print("-> Expected: Normal Red fading to Black restored.\n")

input("[Press ENTER] Step 4: Set effect to Static (monocolor)")
write_sysfs("effect", "monocolor")
print("-> Expected: Solid Red. No Blue should bleed into static mode.\n")

print("Test complete!")
