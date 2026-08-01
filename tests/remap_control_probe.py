#!/usr/bin/env python3
# remap_control_probe.py - the three controls the earlier probes skipped.
#
# Everything so far has tested ONE button (the M1/M2 pair, 0x08) through ONE
# subopcode (CMD_SET_MAPPING, 0x02), and concluded "the MCU ignores it". That
# conclusion has never been checked against a control, so it cannot distinguish
# between three very different situations:
#
#   - the mapping subsystem is dead device-wide
#   - the mapping subsystem works and the PADDLES are specially overridden
#   - config writes in general silently no-op on this device
#
# Worse, the gamepad-mode test was self-confirming. gamepad_mode_show returns
# the DRIVER'S CACHE, not the device state:
#
#     cfg = ally->config;
#     mode_byte = cfg->gamepad_mode;        <- hid-asus.c, gamepad_mode_show
#
# and gamepad_mode_store sets that cache as soon as the USB transfer returns 0 -
# the same false success we are chasing. So "mode now reads: desktop" proved
# only that the driver wrote it down.
#
# Three controls:
#   [1] MODE   - switch to desktop and press A. In desktop mode the face
#                buttons become keyboard input, so the reports MUST change if
#                the switch really reached the MCU. If they do not, config
#                writes fail broadly and this was never a remap-specific bug.
#   [2] BUTTON - remap btn_a (pair 0x05) via the driver's own sysfs and press
#                A. If A remaps, mapping works and the paddles are special.
#                Tries a gamepad target and a keyboard target, because the
#                remap that demonstrably worked on Windows was keyboard-typed
#                (M1=Tab), and Nero reports PAD_* targets failing on his Ally.
#   [3] TURBO  - set btn_m1/turbo_period and hold M1. Turbo is per-button
#                config for the SAME button through a DIFFERENT subopcode
#                (0x0F, and CheckTurboSupport reads SUPPORTED here). If turbo
#                works on M1, button addressing is fine and only 0x02 is
#                refused.
#
# All changes are restored on exit, including on Ctrl-C.
#
# Reading raw HID means analog sticks add noise, so reports are summarised by
# byte position: positions that took 2-4 distinct values are buttons, positions
# that took many are analog. Keep your thumbs OFF the sticks during captures.
#
# NOTE: desktop mode turns the sticks and face buttons into keyboard/mouse
# input. Focus an empty desktop area, not a text field, before running.
#
# Usage: sudo python3 remap_control_probe.py

import glob
import os
import select
import sys
import time

REPORT_ID = 0x5A


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


def read_attr(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def write_attr(path, value):
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError as e:
        print(f"    write {value!r} -> {os.path.basename(path)} failed: {e.strerror}")
        return False


def capture(nodes, seconds, prompt):
    """Collect raw reports per interface for `seconds`."""
    fds, names = [], []
    for num, node in sorted(nodes.items()):
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
            names.append(f"1.{num}")
        except OSError:
            pass
    print(f"  >>> {prompt} ({seconds}s)...")
    per_iface = {n: [] for n in names}
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            ready, _, _ = select.select(fds, [], [], max(0, deadline - time.time()))
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except (BlockingIOError, OSError):
                    continue
                if data:
                    per_iface[names[fds.index(fd)]].append(bytes(data))
    finally:
        for fd in fds:
            os.close(fd)
    return per_iface


def signature(per_iface):
    """Summarise: byte positions taking 2-4 distinct values are buttons;
    positions taking many are analog noise and get dropped."""
    sig = {}
    for name, reports in per_iface.items():
        if not reports:
            continue
        width = min(len(r) for r in reports)
        interesting = {}
        for pos in range(width):
            vals = {r[pos] for r in reports}
            if 2 <= len(vals) <= 4:
                interesting[pos] = tuple(sorted(vals))
        sig[name] = (len(reports), interesting)
    return sig


def show(sig, label):
    if not sig:
        print("    (no reports seen)")
    for name, (count, interesting) in sorted(sig.items()):
        if interesting:
            desc = ", ".join(f"byte{p}={'/'.join(f'{v:02x}' for v in vals)}"
                             for p, vals in sorted(interesting.items()))
        else:
            desc = "no button-like byte changed"
        print(f"    [{name}] {count} reports; {desc}")
    print(f"  == {label} ==\n")


def compare(a, b, what):
    if a == b:
        print(f"    -> NO CHANGE: {what}\n")
        return False
    print(f"    *** CHANGED: {what} ***\n")
    return True


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    if not nodes:
        sys.exit("No ROG Ally X hidraw nodes found.")

    mode_p = sysfs("gamepad_mode")
    a_remap_p = sysfs("btn_a/remap")
    m1_turbo_p = sysfs("btn_m1/turbo_period")

    orig_mode = read_attr(mode_p) if mode_p else None
    orig_a = read_attr(a_remap_p) if a_remap_p else None
    orig_turbo = read_attr(m1_turbo_p) if m1_turbo_p else None

    print("interfaces :", ", ".join(f"1.{n}" for n in sorted(nodes)))
    print(f"gamepad_mode      = {orig_mode}")
    print(f"btn_a/remap       = {orig_a}")
    print(f"btn_m1/turbo_period = {orig_turbo}")
    print("\nKeep your thumbs OFF the sticks during captures.\n")

    try:
        # ---- control 1: did the mode switch ever reach the MCU? ---------
        print("[1] MODE SWITCH - press A in each mode")
        print("    In desktop mode the face buttons become keyboard input, so")
        print("    the reports must change if the switch reached the MCU.\n")
        if not mode_p:
            print("    gamepad_mode not exposed - skipping\n")
            gamepad_sig = desktop_sig = None
        else:
            write_attr(mode_p, "gamepad")
            time.sleep(0.5)
            gamepad_sig = signature(capture(nodes, 5, "press A a few times (gamepad mode)"))
            show(gamepad_sig, "A in gamepad mode")

            write_attr(mode_p, "desktop")
            time.sleep(0.8)
            desktop_sig = signature(capture(nodes, 5, "press A a few times (desktop mode)"))
            show(desktop_sig, "A in desktop mode")
            compare(gamepad_sig, desktop_sig,
                    "the mode switch really reached the device")

            write_attr(mode_p, "gamepad")
            time.sleep(0.8)

        # ---- control 2: does ANY other button remap? --------------------
        print("[2] BUTTON REMAP - btn_a, via the driver's own sysfs")
        if not a_remap_p:
            print("    btn_a/remap not exposed - skipping\n")
        else:
            base = signature(capture(nodes, 5, "press A a few times (stock binding)"))
            show(base, "A stock")

            for target in ("PAD_B", "KB_F8"):
                if not write_attr(a_remap_p, target):
                    continue
                readback = read_attr(a_remap_p)
                print(f"    btn_a/remap now reads: {readback}")
                time.sleep(0.4)
                sig = signature(capture(nodes, 5, f"press A a few times (A -> {target})"))
                show(sig, f"A remapped to {target}")
                compare(base, sig, f"btn_a responded to the {target} remap")

            write_attr(a_remap_p, orig_a or "PAD_A")

        # ---- control 3: does turbo work on M1? --------------------------
        print("[3] TURBO ON M1 - same button, different subopcode (0x0F)")
        if not m1_turbo_p:
            print("    btn_m1/turbo_period not exposed - skipping\n")
        else:
            before = capture(nodes, 4, "HOLD M1 down the whole time (turbo off)")
            n_before = sum(1 for r in before.get("1.2", []) if len(r) > 1 and r[1] == 0xa5)
            print(f"    a5 reports while holding, turbo off: {n_before}")
            show(signature(before), "M1 held, turbo off")

            if write_attr(m1_turbo_p, "5"):
                print(f"    btn_m1/turbo_period now reads: {read_attr(m1_turbo_p)}")
                time.sleep(0.4)
                after = capture(nodes, 4, "HOLD M1 down the whole time (turbo = 5)")
                n_after = sum(1 for r in after.get("1.2", []) if len(r) > 1 and r[1] == 0xa5)
                print(f"    a5 reports while holding, turbo on : {n_after}")
                show(signature(after), "M1 held, turbo on")
                if n_after > n_before + 2:
                    print("    *** TURBO WORKS: the MCU is auto-repeating M1 ***\n")
                else:
                    print("    -> NO CHANGE: turbo did not take effect either\n")
    finally:
        print("[R] RESTORE")
        if a_remap_p and orig_a:
            write_attr(a_remap_p, orig_a)
            print(f"    btn_a/remap -> {read_attr(a_remap_p)}")
        if m1_turbo_p and orig_turbo is not None:
            write_attr(m1_turbo_p, orig_turbo)
            print(f"    btn_m1/turbo_period -> {read_attr(m1_turbo_p)}")
        if mode_p and orig_mode:
            write_attr(mode_p, orig_mode)
            time.sleep(0.5)
            print(f"    gamepad_mode -> {read_attr(mode_p)}")

    print("""
How to read this:
  [1] no change between modes
      -> the mode switch never reached the MCU either. Config writes fail
         broadly on this device, this was never a remap-specific bug, and
         every mode-dependent conclusion so far is void.
  [1] changed, [2] no change
      -> config writes DO work; CMD_SET_MAPPING specifically is refused.
  [2] btn_a changed but the paddles never do
      -> the mapping subsystem is fine and the paddles are specially
         overridden, which points at the persisted "use as secondary
         function" routing rather than anything in our driver.
  [3] turbo works while remap does not
      -> per-button config reaches M1 fine; only subopcode 0x02 is being
         ignored, which is a firmware-level refusal we cannot argue with.
""")


if __name__ == "__main__":
    main()
