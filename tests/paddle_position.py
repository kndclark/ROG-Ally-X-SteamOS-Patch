#!/usr/bin/env python3
# paddle_position.py - which PHYSICAL paddle does btn_m1 control?
#
# Two earlier tests disagree:
#
#   paddle_slot_order.py : wrote btn_m1=KB_F8, "M1" emitted F8
#                          -> btn_m1 controls M1
#   manual F15/F14 test  : wrote btn_m1=KB_F15 and btn_m2=KB_F14,
#                          "M1" emitted F14
#                          -> btn_m1 controls M2
#
# Both rely on the operator knowing which physical paddle is labelled M1, so a
# single mix-up flips the answer. This test never uses those labels. It writes
# two codes that are trivially distinguishable and asks only about physical
# position, which cannot be misremembered:
#
#   btn_m1 -> KB_F15  (HID usage 0x6a)
#   btn_m2 -> KB_F14  (HID usage 0x69)
#
# Whichever side emits 0x6a is the side btn_m1 actually drives. That is the
# whole experiment.
#
# For reference, InputPlumber's aly1 capability map turns these into paddles:
#   KeyF14 -> LeftPaddle1  (shown as L4 in Steam)
#   KeyF15 -> RightPaddle1 (shown as R4 in Steam)
#
# Original bindings are restored on exit, including on Ctrl-C.
#
# Usage: sudo python3 paddle_position.py

import glob
import os
import select
import sys
import time

USAGE_F14, USAGE_F15 = 0x69, 0x6a
NAMES = {USAGE_F14: "F14 (InputPlumber: LeftPaddle1 / L4)",
         USAGE_F15: "F15 (InputPlumber: RightPaddle1 / R4)"}


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


def write_attr(p, v):
    try:
        with open(p, "w") as f:
            f.write(v)
        return True
    except OSError as e:
        print(f"    write {v!r} failed: {e.strerror}")
        return False


def capture(nodes, seconds, prompt):
    fds, names = [], []
    for num, node in sorted(nodes.items()):
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
            names.append(f"1.{num}")
        except OSError:
            pass
    print(f"\n  >>> {prompt}")
    print(f"      you have {seconds} seconds, press it a few times...")
    usages, vendor = set(), set()
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
                if data[0] == 0x01 and len(data) > 3 and data[3]:
                    usages.add(data[3])
                elif data[0] == 0x5a and len(data) > 1 and data[1]:
                    vendor.add(data[1])
    finally:
        for fd in fds:
            os.close(fd)
    if usages:
        for u in sorted(usages):
            print(f"      -> {NAMES.get(u, f'usage 0x{u:02x}')}")
    elif vendor:
        for v in sorted(vendor):
            print(f"      -> vendor 0x{v:02x} (still on its stock binding)")
    else:
        print("      -> nothing captured")
    return usages


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    m1_p, m2_p = sysfs("btn_m1/remap"), sysfs("btn_m2/remap")
    if not nodes or not m1_p or not m2_p:
        sys.exit("Ally X hidraw nodes or remap attributes not found.")

    orig_m1, orig_m2 = read_attr(m1_p), read_attr(m2_p)
    print(f"btn_m1/remap = {orig_m1}   (will set to KB_F15, usage 0x6a)")
    print(f"btn_m2/remap = {orig_m2}   (will set to KB_F14, usage 0x69)")

    try:
        write_attr(m1_p, "KB_F15")
        write_attr(m2_p, "KB_F14")
        time.sleep(0.5)
        print(f"\nreadback: btn_m1={read_attr(m1_p)}  btn_m2={read_attr(m2_p)}")
        print("\nIgnore the M1/M2 labels completely. Answer only by position:")
        print("holding the device normally, which paddle is under which hand.")

        left = capture(nodes, 6,
                       "press the LEFT back paddle (left hand side of the device)")
        right = capture(nodes, 6,
                        "press the RIGHT back paddle (right hand side of the device)")
    finally:
        print("\n[R] RESTORE")
        write_attr(m1_p, orig_m1 or "KB_M1")
        write_attr(m2_p, orig_m2 or "KB_M2")
        print(f"    btn_m1/remap -> {read_attr(m1_p)}")
        print(f"    btn_m2/remap -> {read_attr(m2_p)}")

    print("\nresult")
    print("-" * 62)
    if USAGE_F15 in left and USAGE_F14 in right:
        print("""  btn_m1 drives the LEFT paddle, btn_m2 drives the RIGHT one.

  The first entry of the mapping block is therefore M1's slot, which means
  the swap fix is BACKWARDS and must be reverted before the patch is sent.""")
    elif USAGE_F15 in right and USAGE_F14 in left:
        print("""  btn_m1 drives the RIGHT paddle, btn_m2 drives the LEFT one.

  That matches hid-asus-ally (m2 -> btn_pair_side_left) and G-Helper
  (bind_m2 at offset 5), so the swap fix is CORRECT as written and the
  earlier F15/F14 confusion was a mislabelled paddle press.""")
    elif not left or not right:
        print("  Inconclusive - one side produced nothing. Re-run and make sure")
        print("  each paddle is pressed during its own window.")
    else:
        print(f"  Unexpected: left={sorted(left)} right={sorted(right)}")
        print("  Both sides may be emitting the same code; check the readback above.")


if __name__ == "__main__":
    main()
