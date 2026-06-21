#!/usr/bin/env python3
import os

LED_DIR = "/sys/class/leds/go_s:rgb:joystick_rings"

def write_sysfs(attr, val):
    try:
        with open(os.path.join(LED_DIR, attr), 'w') as f:
            f.write(str(val))
    except PermissionError:
        print(f"Permission denied writing to {attr}. Did you run with sudo?")
        exit(1)

print("--- ROG Ally X: Direction (Phase 4) Test ---")
print("Press ENTER to proceed through each step.\n")

# RAINBOW TEST
print("--- Testing RAINBOW effect ---")
write_sysfs("effect", "rainbow")
write_sysfs("direction", "forward")

input("[Press ENTER] Step 1: Default Rainbow")
print("-> Expected: Rings cycle colors in the standard clockwise forward direction.\n")

input("[Press ENTER] Step 2: Set direction to Reverse")
write_sysfs("direction", "reverse")
print("-> Expected: Rings instantly switch to cycling counter-clockwise.\n")

input("[Press ENTER] to finish Rainbow test and move to Chroma")

# CHROMA TEST
print("--- Testing CHROMA (Color Cycle) effect ---")
write_sysfs("effect", "chroma")
write_sysfs("direction", "forward")

input("[Press ENTER] Step 3: Default Chroma")
print("-> Expected: Solid color slowly fades to the next color in the standard sequence.\n")

input("[Press ENTER] Step 4: Set direction to Reverse")
write_sysfs("direction", "reverse")
print("-> Expected: Solid color fades backwards through the sequence.\n")

input("[Press ENTER] to finish testing and clean up")
print("Cleaning up...")
write_sysfs("effect", "breathe")
write_sysfs("multi_intensity", "255 0 0")
write_sysfs("color2", "0 0 0")
write_sysfs("direction", "forward")
print("Test complete!")
