#!/usr/bin/env python3
# paddle_identity.py - what do M1 and M2 actually emit right now?
#
# READ-ONLY. Writes nothing, changes no binding, sends no packet.
#
# Two ways a paddle can report:
#
#   1. Vendor code on the config interface (1.2), report 0x5A. This is the
#      stock "M button" binding. The driver only knows ONE paddle vendor code:
#
#          case 0xa5: asus_map_key_clear(KEY_F15);  /* ROG Ally left back  */
#          case 0xa6: asus_map_key_clear(KEY_F16);  /* ROG Ally QAM button */
#
#      Note 0xa6 is the QAM button, NOT the right paddle - there is no second
#      paddle vendor code. So both paddles sitting on 0xa5 in their stock
#      binding is expected, not a fault: ASUS's design is that you remap them,
#      which is what Armoury Crate does on Windows (M1=Tab, M2=Esc).
#
#   2. HID keyboard report on the keyboard interface (1.3), report 0x01, with
#      the usage in byte 3. This is what a KB_* remap produces, and it is how
#      the paddles become independently usable.
#
# Usage: sudo python3 paddle_identity.py

import glob
import os
import select
import sys
import time

VENDOR_REPORT_ID = 0x5A
KEYBOARD_REPORT_ID = 0x01

VENDOR_CODES = {
    0xa5: "0xa5 -> KEY_F15 (the stock paddle 'M button' code)",
    0xa6: "0xa6 -> KEY_F16 (this is the QAM button, not a paddle)",
    0xa7: "0xa7 -> KEY_F17 (ROG long-press)",
}

# HID keyboard usage IDs, enough to name whatever a remap is likely to produce.
USAGES = {0x29: "Esc", 0x2b: "Tab", 0x2c: "Space"}
for _i, _n in enumerate(range(0x3a, 0x46)):      # F1..F12
    USAGES[_n] = f"F{_i + 1}"
for _i, _n in enumerate(range(0x68, 0x74)):      # F13..F24
    USAGES[_n] = f"F{_i + 13}"


def ally_interfaces():
    found = {}
    for hr in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        path = os.path.realpath(hr)
        while path != "/":
            bin_file = os.path.join(path, "bInterfaceNumber")
            if os.path.exists(bin_file):
                try:
                    num = int(open(bin_file).read().strip(), 16)
                    parent = os.path.dirname(path)
                    vid = open(os.path.join(parent, "idVendor")).read().strip()
                    pid = open(os.path.join(parent, "idProduct")).read().strip()
                except OSError:
                    break
                if vid == "0b05" and pid == "1b4c":
                    found.setdefault(num, "/dev/" + os.path.basename(hr))
                break
            path = os.path.dirname(path)
    return found


def sysfs(name):
    hits = glob.glob(f"/sys/bus/hid/devices/*1B4C*/{name}")
    return hits[0] if hits else None


def read_attr(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None


def describe(vendor, keys):
    out = []
    for c in sorted(vendor):
        out.append(VENDOR_CODES.get(c, f"vendor 0x{c:02x} (unknown)"))
    for k in sorted(keys):
        out.append(f"keyboard usage 0x{k:02x} ({USAGES.get(k, 'unnamed')})")
    return out or ["nothing"]


def capture(nodes, seconds, label):
    fds, names = [], []
    for num, node in sorted(nodes.items()):
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
            names.append(f"1.{num}")
        except OSError:
            pass
    print(f"  >>> press and release {label} a few times ({seconds}s)...")
    vendor, keys, seen = set(), set(), []
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            ready, _, _ = select.select(fds, [], [], max(0, deadline - time.time()))
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except (BlockingIOError, OSError):
                    continue
                if not data:
                    continue
                line = f"[{names[fds.index(fd)]}] " + " ".join(f"{b:02x}" for b in data[:6])
                if line not in seen:
                    print(f"    {line}")
                seen.append(line)
                if data[0] == VENDOR_REPORT_ID and len(data) > 1 and data[1]:
                    vendor.add(data[1])
                elif data[0] == KEYBOARD_REPORT_ID and len(data) > 3 and data[3]:
                    keys.add(data[3])
    finally:
        for fd in fds:
            os.close(fd)
    if not seen:
        print("    (no reports seen - was the paddle pressed?)")
    print(f"  == {label}: {'; '.join(describe(vendor, keys))} ==\n")
    return vendor, keys


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    if not nodes:
        sys.exit("No ROG Ally X hidraw nodes found.")

    print(f"btn_m1/remap = {read_attr(sysfs('btn_m1/remap') or '') or '?'}")
    print(f"btn_m2/remap = {read_attr(sysfs('btn_m2/remap') or '') or '?'}")
    print("\nNothing is written. This only listens.\n")

    m1 = capture(nodes, 5, "M1")
    m2 = capture(nodes, 5, "M2")

    print("result")
    print("-" * 62)
    print(f"  M1: {'; '.join(describe(*m1))}")
    print(f"  M2: {'; '.join(describe(*m2))}")
    print()

    if not any(m1) or not any(m2):
        print("  Inconclusive - at least one paddle produced nothing. Re-run and")
        print("  make sure both are pressed during their own window.")
    elif m1 == m2:
        print("""  IDENTICAL. Both paddles emit the same thing, so nothing downstream
  can tell them apart. If they are both on the stock KB_M1/KB_M2 binding
  this is expected - remap them to distinct KB_* codes to separate them.""")
    else:
        print("""  DISTINCT. The paddles emit different codes, so they are independently
  addressable and usable as two separate buttons in game.""")


if __name__ == "__main__":
    main()
