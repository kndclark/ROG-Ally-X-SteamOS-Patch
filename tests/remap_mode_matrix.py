#!/usr/bin/env python3
# remap_mode_matrix.py - is button remapping blocked by xbox_controller mode?
#
# Nero, on his Ally:
#   "I cannot set BTN_A to anything. Silently rejected. I cannot set any button
#    on xbox to anything else: only desktop mode."
#
# There are two independent mode controls and we have been conflating them:
#   gamepad_mode     gamepad / desktop / mouse   (CMD_SET_GAMEPAD_MODE, 0x01)
#   xbox_controller  1 / 0                       (CMD_SET_XBOX_CONTROLLER, 0x0B)
#
# The driver forces xbox_controller on at probe. A plausible mechanism for his
# report: in xbox mode the MCU presents a standard Xbox controller, so the face
# buttons belong to that report and cannot be remapped, while M1/M2 are not part
# of the Xbox spec and stay remappable. That predicts btn_a works only with
# xbox_controller=0, or only in desktop mode.
#
# This walks all four combinations and measures each one:
#
#     xbox_controller x gamepad_mode -> can btn_a be remapped?
#
# KB_F8 is the probe code because it surfaces on the keyboard interface (1.3)
# as HID usage 0x41, which we can actually read. PAD_* codes surface on the
# gamepad interface, which has never produced a report in any test here, so
# this deliberately does NOT test PAD_* - a null result there would be
# meaningless rather than informative.
#
# Both mode settings and btn_a are restored on exit, including on Ctrl-C.
#
# Usage: sudo python3 remap_mode_matrix.py

import glob
import os
import select
import sys
import time

TARGET, TARGET_USAGE = "KB_F8", 0x41


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
    except OSError as e:
        return f"<unreadable: {e.strerror}>"


def write_attr(p, v):
    """Returns (ok, errno_string). Silent rejection is the thing under test,
    so a failed write must be reported, never swallowed."""
    try:
        with open(p, "w") as f:
            f.write(v)
        return True, ""
    except OSError as e:
        return False, e.strerror


def capture(nodes, seconds, prompt):
    fds, names = [], []
    for num, node in sorted(nodes.items()):
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
            names.append(f"1.{num}")
        except OSError as e:
            print(f"      (could not open 1.{num}: {e.strerror})")
    print(f"    >>> {prompt} ({seconds}s)...")
    usages, total = set(), 0
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
                total += 1
                if data[0] == 0x01 and len(data) > 3 and data[3]:
                    usages.add(data[3])
                elif data[0] == 0x5a and len(data) > 1 and data[1]:
                    usages.add(("vendor", data[1]))
    finally:
        for fd in fds:
            os.close(fd)
    print(f"      {total} reports; codes seen: "
          f"{sorted(str(u) for u in usages) or 'none'}")
    return usages


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    a_p, mode_p, xbox_p = sysfs("btn_a/remap"), sysfs("gamepad_mode"), sysfs("xbox_controller")
    if not nodes or not a_p or not mode_p or not xbox_p:
        sys.exit("Needed sysfs attributes not found (btn_a/remap, gamepad_mode, xbox_controller).")

    orig_a, orig_mode, orig_xbox = read_attr(a_p), read_attr(mode_p), read_attr(xbox_p)
    print(f"starting state: xbox_controller={orig_xbox}  gamepad_mode={orig_mode}  btn_a={orig_a}")
    print("\nDesktop mode turns the face buttons into keyboard input - make sure")
    print("no text field is focused before you start.\n")

    results = {}
    try:
        for xbox in ("1", "0"):
            for mode in ("gamepad", "desktop"):
                print(f"=== xbox_controller={xbox}  gamepad_mode={mode} ===")
                ok, err = write_attr(xbox_p, xbox)
                if not ok:
                    print(f"    xbox_controller write REJECTED: {err}")
                    results[(xbox, mode)] = "xbox write rejected"
                    continue
                ok, err = write_attr(mode_p, mode)
                if not ok:
                    print(f"    gamepad_mode write REJECTED: {err}")
                    results[(xbox, mode)] = "mode write rejected"
                    continue
                time.sleep(1.0)
                print(f"    readback: xbox={read_attr(xbox_p)} mode={read_attr(mode_p)}")

                ok, err = write_attr(a_p, TARGET)
                if not ok:
                    print(f"    btn_a/remap write REJECTED: {err}")
                    results[(xbox, mode)] = f"remap write rejected ({err})"
                    continue
                back = read_attr(a_p)
                print(f"    btn_a/remap reads back: {back}")
                time.sleep(0.4)

                usages = capture(nodes, 5, "press A a few times")
                if TARGET_USAGE in usages:
                    results[(xbox, mode)] = "REMAP WORKS"
                elif not usages:
                    results[(xbox, mode)] = "nothing observed (blind)"
                else:
                    results[(xbox, mode)] = "remap ignored"
                print(f"    -> {results[(xbox, mode)]}\n")

                write_attr(a_p, orig_a)
                time.sleep(0.3)
    finally:
        print("[R] RESTORE")
        write_attr(a_p, orig_a)
        write_attr(xbox_p, orig_xbox)
        write_attr(mode_p, orig_mode)
        time.sleep(0.8)
        print(f"    xbox_controller={read_attr(xbox_p)}  "
              f"gamepad_mode={read_attr(mode_p)}  btn_a={read_attr(a_p)}")

    print("\nmatrix")
    print("-" * 62)
    print(f"  {'xbox':<6} {'mode':<10} result")
    for (xbox, mode), r in results.items():
        print(f"  {xbox:<6} {mode:<10} {r}")
    print("""
  If btn_a remaps with xbox_controller=0 but not =1, Nero's report reproduces
  here and the driver forcing xbox mode on at probe is what blocks remapping.
  If it remaps in every combination, the difference is his device or the
  original Ally's firmware, which is what he asked us to find out.""")


if __name__ == "__main__":
    main()
