#!/usr/bin/env python3
# pad_remap_test.py - answer Nero's question: does writing PAD_B to btn_a work?
#
#   "Please try out remapping buttons too. I need to know if it's just my ally
#    or not. Write PAD_B to btn_a/remap"
#
# Three previous attempts at this failed because each one guessed a single
# observation channel and guessed wrong:
#
#   1. hidraw, all interfaces  - gamepad reports never reach hidraw. hid-asus
#      returns -1 for them, and in hid_input_report() a negative raw_event
#      return does `goto unlock` BEFORE hid_report_raw_event(), which is what
#      feeds hidraw. Structurally invisible, on any machine.
#   2. hidraw again, with InputPlumber stopped - same reason, same result.
#   3. evdev event16 ("ASUS ROG Ally X Gamepad") - nothing captured.
#
# There are two input devices on interface 1.5: the generic HID one and the
# driver's own. So this stops guessing: it opens EVERY Ally event node at once,
# reports which ones opened, and shows which ones carry A and B. Only then does
# it run the actual remap test, on whichever node proved to carry the buttons.
#
# It also lists any process holding each node, since an exclusive EVIOCGRAB
# elsewhere looks identical to a dead button.
#
# btn_a and InputPlumber are restored on exit, including on Ctrl-C.
#
# Usage: sudo python3 pad_remap_test.py

import glob
import os
import select
import struct
import subprocess
import sys
import time

EV_FORMAT = "llHHi"
EV_SIZE = struct.calcsize(EV_FORMAT)
EV_KEY = 0x01

BTN_NAMES = {
    0x130: "BTN_SOUTH (A)", 0x131: "BTN_EAST (B)", 0x133: "BTN_NORTH (X)",
    0x134: "BTN_WEST (Y)", 0x136: "BTN_TL (LB)", 0x137: "BTN_TR (RB)",
    0x13a: "BTN_SELECT (View)", 0x13b: "BTN_START (Menu)",
    0x13c: "BTN_MODE (Xbox)", 0x13d: "BTN_THUMBL (L3)", 0x13e: "BTN_THUMBR (R3)",
}


def ally_event_nodes():
    """Every event node belonging to the Ally, by USB path or by name."""
    out = {}
    for inp in sorted(glob.glob("/sys/class/input/input*")):
        try:
            name = open(os.path.join(inp, "name")).read().strip()
        except OSError:
            continue
        phys = ""
        try:
            phys = open(os.path.join(inp, "phys")).read().strip()
        except OSError:
            pass
        hit = ("Ally" in name) or ("N-KEY" in name) or ("-2/input" in phys)
        if not hit:
            continue
        for ev in glob.glob(os.path.join(inp, "event*")):
            out["/dev/input/" + os.path.basename(ev)] = name
    return out


def holders(path):
    """Processes with this node open, best effort."""
    found = []
    for pid in glob.glob("/proc/[0-9]*"):
        try:
            for fd in glob.glob(os.path.join(pid, "fd", "*")):
                if os.path.realpath(fd) == path:
                    comm = open(os.path.join(pid, "comm")).read().strip()
                    found.append(f"{comm}({os.path.basename(pid)})")
                    break
        except (OSError, PermissionError):
            continue
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


def capture(fds, paths, seconds, prompt):
    """Return {path: set(keycodes)} for press events."""
    print(f"\n  >>> {prompt} ({seconds}s)...")
    per = {p: set() for p in paths}
    deadline = time.time() + seconds
    while time.time() < deadline:
        ready, _, _ = select.select(fds, [], [], max(0, deadline - time.time()))
        for fd in ready:
            try:
                data = os.read(fd, EV_SIZE * 64)
            except (BlockingIOError, OSError):
                continue
            path = paths[fds.index(fd)]
            for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                _, _, etype, code, value = struct.unpack(
                    EV_FORMAT, data[off:off + EV_SIZE])
                if etype == EV_KEY and value == 1:
                    per[path].add(code)
    any_seen = False
    for path in paths:
        if per[path]:
            any_seen = True
            names = ", ".join(BTN_NAMES.get(c, f"code {c}") for c in sorted(per[path]))
            print(f"      {os.path.basename(path):<10} {names}")
    if not any_seen:
        print("      nothing on any node")
    return per


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_event_nodes()
    a_p = sysfs("btn_a/remap")
    if not nodes or not a_p:
        sys.exit("No Ally event nodes, or btn_a/remap missing.")
    orig_a = read_attr(a_p)

    print("Ally event nodes:")
    for path, name in sorted(nodes.items()):
        h = holders(path)
        print(f"  {os.path.basename(path):<10} {name:<38} "
              f"{'held by ' + ', '.join(h) if h else 'free'}")
    print(f"\nbtn_a/remap = {orig_a}")

    ip_running = subprocess.run(
        ["systemctl", "is-active", "--quiet", "inputplumber"]).returncode == 0
    if ip_running:
        print("\nstopping inputplumber...")
        subprocess.run(["systemctl", "stop", "inputplumber"])
        time.sleep(1.5)
        print("  holders after stop:")
        for path in sorted(nodes):
            h = holders(path)
            if h:
                print(f"    {os.path.basename(path):<10} still held by {', '.join(h)}")

    fds, paths = [], []
    try:
        print("\nopening nodes:")
        for path in sorted(nodes):
            try:
                fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
                paths.append(path)
                print(f"  {os.path.basename(path):<10} OK")
            except OSError as e:
                print(f"  {os.path.basename(path):<10} FAILED: {e.strerror}")
        if not fds:
            sys.exit("Could not open any node.")

        print("\n[1] WHICH node carries A and B, while both are stock")
        a_stock = capture(fds, paths, 5, "press A a few times")
        b_stock = capture(fds, paths, 5, "press B a few times")

        carriers = [p for p in paths if a_stock[p] and b_stock[p]
                    and a_stock[p] != b_stock[p]]
        if not carriers:
            print("""
    No node reported A and B as distinct keycodes, so the remap test cannot
    run. Check the holder list above - if something still has the node with
    EVIOCGRAB, that looks exactly like a dead button.""")
            return
        target = carriers[0]
        print(f"\n    using {os.path.basename(target)} "
              f"(A={sorted(hex(c) for c in a_stock[target])}, "
              f"B={sorted(hex(c) for c in b_stock[target])})")

        print("\n[2] REMAP btn_a -> PAD_B, then press A")
        if not write_attr(a_p, "PAD_B"):
            return
        print(f"    btn_a/remap now reads: {read_attr(a_p)}")
        time.sleep(0.5)
        for fd in fds:
            try:
                os.read(fd, EV_SIZE * 256)
            except (BlockingIOError, OSError):
                pass
        a_remapped = capture(fds, paths, 5, "press A a few times")

        print("\nresult")
        print("-" * 62)
        got, was, b_was = a_remapped[target], a_stock[target], b_stock[target]
        if got == b_was:
            print("  PAD_B WORKS. Pressing A now reports B's keycode - the MCU")
            print("  accepted the remap. Nero's report does not reproduce here.")
        elif got == was:
            print("  PAD_B IGNORED. A still reports its own keycode, so the write")
            print("  was accepted and silently did nothing, matching Nero.")
        elif not got:
            print("  A went silent - the remap changed something, but A no longer")
            print("  reports on this node at all.")
        else:
            print("  A changed but matches neither its old code nor B's:")
            print(f"    A stock    : {sorted(hex(c) for c in was)}")
            print(f"    B stock    : {sorted(hex(c) for c in b_was)}")
            print(f"    A remapped : {sorted(hex(c) for c in got)}")
    finally:
        for fd in fds:
            os.close(fd)
        print("\n[R] RESTORE")
        write_attr(a_p, orig_a or "PAD_A")
        print(f"    btn_a/remap -> {read_attr(a_p)}")
        if ip_running:
            subprocess.run(["systemctl", "start", "inputplumber"])
            print("    inputplumber restarted")


if __name__ == "__main__":
    main()
