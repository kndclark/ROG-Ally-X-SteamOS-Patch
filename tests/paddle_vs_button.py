#!/usr/bin/env python3
# paddle_vs_button.py - the controlled comparison the earlier probes never ran.
#
# remap_control_probe.py established that CMD_SET_MAPPING works on the Ally X:
# btn_a remapped to KB_F8 produced HID keyboard usage 0x41 (F8) on interface
# 1.3. It also showed that per-button config reaches M1 fine, because turbo on
# M1 made the MCU auto-repeat the paddle.
#
# But every paddle remap we ever tried used MEDIA_VOL_UP, while the one target
# proven observable is KB_F8 on interface 1.3. So the paddle tests and the
# working btn_a test were never comparing the same thing, and "the paddle did
# not change" may have meant "we were not watching where media codes land".
#
# This runs the identical remap on several buttons and watches the same place
# for all of them. Same target (KB_F8), same observation point, one variable:
# which button.
#
#   btn_a    - positive control, known to work
#   btn_view - second non-paddle control, to show the scope
#   btn_m1   - the paddle in question
#   btn_m2   - the other paddle
#
# If btn_a and btn_view produce keyboard reports and the paddles do not, the
# paddles are specially exempt from remapping and the cause is device state,
# not our driver - most likely the persisted Armoury Crate "use as secondary
# function" routing documented in investigations/paddle-mapping.md.
#
# Every button is restored to its original binding on exit, including Ctrl-C.
#
# Usage: sudo python3 paddle_vs_button.py

import glob
import os
import select
import sys
import time

TARGET = "KB_F8"
TARGET_USAGE = 0x41      # HID keyboard usage for F8, seen on interface 1.3
BUTTONS = ["btn_a", "btn_view", "btn_m1", "btn_m2"]


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
        print(f"    write {value!r} failed: {e.strerror}")
        return False


def capture(nodes, seconds, prompt):
    fds, names = [], []
    for num, node in sorted(nodes.items()):
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
            names.append(f"1.{num}")
        except OSError:
            pass
    print(f"    >>> {prompt} ({seconds}s)...")
    out = {n: [] for n in names}
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
                    out[names[fds.index(fd)]].append(bytes(data))
    finally:
        for fd in fds:
            os.close(fd)
    return out


def summarise(per_iface):
    """Report every interface that saw traffic, and whether the F8 usage
    appeared anywhere in it."""
    lines, saw_target = [], False
    for name, reports in sorted(per_iface.items()):
        if not reports:
            continue
        hit = any(TARGET_USAGE in r for r in reports)
        saw_target = saw_target or hit
        sample = " ".join(f"{b:02x}" for b in reports[0][:8])
        mark = "  <- F8 (0x41) present" if hit else ""
        lines.append(f"      [{name}] {len(reports)} reports, first: {sample}{mark}")
    if not lines:
        lines.append("      (no reports on any interface)")
    return lines, saw_target


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    if not nodes:
        sys.exit("No ROG Ally X hidraw nodes found.")

    paths, original = {}, {}
    for btn in BUTTONS:
        p = sysfs(f"{btn}/remap")
        if not p:
            print(f"warning: {btn}/remap not exposed, skipping")
            continue
        paths[btn] = p
        original[btn] = read_attr(p)

    if not paths:
        sys.exit("No remap attributes found - is the driver loaded?")

    print(f"target        : {TARGET} (HID usage 0x{TARGET_USAGE:02x})")
    print(f"interfaces    : {', '.join(f'1.{n}' for n in sorted(nodes))}")
    for btn in paths:
        print(f"{btn:<14}: currently {original[btn]}")
    print("\nKeep your thumbs OFF the sticks during captures.\n")

    results = {}
    try:
        for btn in paths:
            print(f"--- {btn} ---")
            if not write_attr(paths[btn], TARGET):
                continue
            print(f"    remap reads back: {read_attr(paths[btn])}")
            time.sleep(0.4)
            label = btn.replace("btn_", "").upper()
            per_iface = capture(nodes, 5, f"press {label} a few times")
            lines, hit = summarise(per_iface)
            for line in lines:
                print(line)
            results[btn] = hit
            print(f"    => {'REMAP TOOK EFFECT' if hit else 'no F8 seen'}\n")
            write_attr(paths[btn], original[btn] or "NONE")
            time.sleep(0.3)
    finally:
        print("[R] RESTORE")
        for btn, p in paths.items():
            if original.get(btn):
                write_attr(p, original[btn])
                print(f"    {btn}/remap -> {read_attr(p)}")

    if results:
        print("\nsummary")
        print("-" * 40)
        for btn, hit in results.items():
            print(f"  {btn:<12} {'remapped' if hit else 'IGNORED'}")
        paddles = [b for b in ("btn_m1", "btn_m2") if b in results]
        others = [b for b in results if b not in ("btn_m1", "btn_m2")]
        if others and paddles:
            if all(results[b] for b in others) and not any(results[b] for b in paddles):
                print("""
  Non-paddle buttons remap, paddles do not, same target, same observation
  point. The paddles are specially exempt from remapping - this is device
  state, not our driver, and the packet was never the problem. Next step is
  finding what Armoury Crate toggles to release them.""")
            elif all(results[b] for b in paddles):
                print("""
  The paddles DO remap with a keyboard target. Every earlier "paddle ignores
  the mapping" result was an artifact of using MEDIA_VOL_UP and watching the
  wrong interface. The driver may need nothing more than a doc fix.""")


if __name__ == "__main__":
    main()
