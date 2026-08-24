#!/usr/bin/env python3
# aura_caps_probe.py - ask the Ally firmware, on every interface, whether it
# implements the laptop Aura capability/status queries the driver tries:
#
#   caps:   5d 9e 01 20   and   5d 9e 01 15
#   status: 5d 05 20 31 00 20   (primed by the same driver sequence)
#
# These are the exact requests hid-asus already sends at probe time; this just
# repeats them on each interface and shows the raw answers, so "the firmware
# does not implement the query" becomes a measured result instead of a guess.
#
# Usage: sudo python3 aura_caps_probe.py

import fcntl
import glob
import os

REPORT_SIZE = 64

def ioc(direction, nr, size):
    # _IOC(dir, 'H', nr, size); dir: 3 = read|write
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr

def set_feature(fd, data):
    buf = bytearray(data)
    fcntl.ioctl(fd, ioc(3, 0x06, len(buf)), buf)

def get_feature(fd, report_id, size=REPORT_SIZE):
    buf = bytearray(size)
    buf[0] = report_id
    fcntl.ioctl(fd, ioc(3, 0x07, size), buf)
    return bytes(buf)

QUERIES = [
    ("caps sel 0x20", [0x5D, 0x9E, 0x01, 0x20]),
    ("caps sel 0x15", [0x5D, 0x9E, 0x01, 0x15]),
    ("status",        [0x5D, 0x05, 0x20, 0x31, 0x00, 0x20]),
]

def main():
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        node = "/dev/" + path.split("/")[-1]
        try:
            uevent = open(path + "/device/uevent").read()
        except OSError:
            continue
        if "0B05" not in uevent.upper() or "1B4C" not in uevent.upper():
            continue
        name = [l.split("=", 1)[1] for l in uevent.splitlines() if l.startswith("HID_NAME=")]
        phys = [l.split("=", 1)[1] for l in uevent.splitlines() if l.startswith("HID_PHYS=")]
        print(f"== {node}  {name[0] if name else '?'}  ({phys[0] if phys else 'no phys'})")

        try:
            fd = os.open(node, os.O_RDWR)
        except OSError as e:
            print(f"   (cannot open: {e})")
            continue

        for label, req in QUERIES:
            pkt = bytes(req) + bytes(REPORT_SIZE - len(req))
            try:
                set_feature(fd, pkt)
                resp = get_feature(fd, 0x5D)
                # echo of our own request back = no answer from the firmware
                if resp[:len(req)] == bytes(req) and not any(resp[len(req):20]):
                    print(f"   {label:14s} -> echoed back (no answer)")
                else:
                    print(f"   {label:14s} -> {resp[:24].hex(' ')}")
            except OSError as e:
                print(f"   {label:14s} -> rejected ({e.strerror})")
        os.close(fd)

if __name__ == "__main__":
    main()
