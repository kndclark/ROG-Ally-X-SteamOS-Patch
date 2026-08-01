#!/usr/bin/env python3
# lamparray_coexist.py - can LampArray colours and Aura effects coexist?
#
# The proposed split is: colours go through the standard HID LampArray path,
# effects (breathe/rainbow/speed/direction) stay on the vendor sysfs path.
# That only works if the two engines do not fight: the MCU has to keep running
# its effect while accepting colours from LampArray.
#
# This walks the interaction in four stages and asks what the rings actually do.
# It writes no NV state; LampArray has no flash-commit concept.
#
# Usage: sudo python3 lamparray_coexist.py

import fcntl
import glob
import os
import struct
import sys
import time

HIDIOCGFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x07
HIDIOCSFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x06
RPT_ATTRS, LEN_ATTRS = 1, 23
RPT_RANGE = 5
RPT_CONTROL = 6
LAMP_UPDATE_COMPLETE = 0x01


def find_lamparray():
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            head = open(os.path.join(path, "device", "report_descriptor"), "rb").read(4)
        except OSError:
            continue
        if head[:4] == bytes([0x06, 0x59, 0x00, 0x09]):
            return "/dev/" + os.path.basename(path)
    return None


def led_dir():
    for d in glob.glob("/sys/class/leds/*rgb*joystick*"):
        return d
    return None


def set_feature(fd, payload):
    buf = bytearray(payload)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)


def write_sysfs(led, attr, value):
    try:
        with open(os.path.join(led, attr), "w") as f:
            f.write(value)
        return True
    except OSError as e:
        print(f"    (sysfs {attr} <- {value!r} failed: {e.strerror})")
        return False


def lamp_colour(fd, count, rgb, label):
    red, green, blue = rgb
    print(f"    LampArray -> {label}")
    try:
        set_feature(fd, struct.pack("<BBHHBBBB", RPT_RANGE, LAMP_UPDATE_COMPLETE,
                                    0, count - 1, red, green, blue, 255))
    except OSError as e:
        print(f"      failed: {e.strerror}")


def ask(question):
    print(f"\n  >>> {question}")
    input("      press Enter to continue... ")


def main():
    dev = find_lamparray()
    led = led_dir()
    if not dev:
        sys.exit("No LampArray collection found.")
    if not led:
        sys.exit("No joystick ring LED found - is the driver loaded?")
    print(f"LampArray : {dev}")
    print(f"LED sysfs : {led}\n")

    fd = os.open(dev, os.O_RDWR)
    try:
        r = bytearray(LEN_ATTRS); r[0] = RPT_ATTRS
        fcntl.ioctl(fd, HIDIOCGFEATURE(LEN_ATTRS), r)
        count = struct.unpack("<H", bytes(r[1:3]))[0]
        print(f"lamp count: {count}")

        # 1. vendor path alone: a running animation
        print("\n[1] vendor sysfs: static red, then breathe")
        write_sysfs(led, "effect", "monochrome")
        write_sysfs(led, "multi_intensity", "255 0 0")
        write_sysfs(led, "brightness", "100")
        time.sleep(1)
        write_sysfs(led, "effect", "breathe")
        write_sysfs(led, "speed", "50")
        ask("Are the rings breathing red?")

        # 2. LampArray colour while the effect runs, autonomous mode untouched
        print("\n[2] LampArray colour WITHOUT taking host control")
        lamp_colour(fd, count, (0, 0, 255), "blue")
        ask("Did the colour change to blue? Is it still breathing?")

        # 3. take host control, then colour
        print("\n[3] AutonomousMode=0 (host control), then LampArray green")
        set_feature(fd, struct.pack("<BB", RPT_CONTROL, 0))
        time.sleep(0.5)
        lamp_colour(fd, count, (0, 255, 0), "green")
        ask("Is it solid green now, or still breathing?")

        # 4. hand back to the device
        print("\n[4] AutonomousMode=1 (device control) again")
        set_feature(fd, struct.pack("<BB", RPT_CONTROL, 1))
        time.sleep(0.5)
        ask("Did the breathing effect resume on its own?")

        # restore something sane through the vendor path
        print("\nrestoring: monochrome white via sysfs")
        write_sysfs(led, "effect", "monochrome")
        write_sysfs(led, "multi_intensity", "255 255 255")
    finally:
        os.close(fd)

    print("""
What the answers mean:
  [2] colour changes AND still breathing -> the two paths coexist; the split
      (colours via LampArray, effects via sysfs) works as proposed.
  [2] no colour change                   -> LampArray needs host control first,
      so check [3].
  [3] solid green, breathing stopped     -> host control disables the effect
      engine: colours and hardware effects are mutually exclusive, and the
      proposed split does NOT work as-is.
  [4] breathing resumes                  -> autonomous mode restores the vendor
      effect cleanly, so the modes are switchable rather than destructive.
""")


if __name__ == "__main__":
    main()
