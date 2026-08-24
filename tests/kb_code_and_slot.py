#!/usr/bin/env python3
# kb_code_and_slot.py - two questions, no driver changes required.
#
# 1. Is the BTN_TYPE_KB code table wrong on the ROG Ally X?
#
#    NeroReflex/linux PR #8 reports, on an Xbox Ally X (RC73XA), that KB_F14
#    and KB_F15 are swapped:
#
#        name written   code sent   emitted usage   key seen
#        KB_F14         0x18        0x6a            KEY_F15
#        KB_F15         0x10        0x69            KEY_F14
#
#    That was a different model. This checks it here by writing each name to
#    btn_a - a front face button with no naming ambiguity - and reading what
#    actually arrives.
#
# 2. Which physical paddle does btn_m1 drive?
#
#    Neither earlier attempt was clean. paddle_slot_order.py used KB_F8 (a code
#    independently verified to emit F8 on this device) but identified buttons
#    by the M1/M2 labels, which are printed on the back where you cannot see
#    them while pressing. paddle_position.py identified buttons by which hand
#    they are under - reliable - but used KB_F15/KB_F14, the two codes question
#    1 is about. One test had trustworthy codes and untrustworthy button
#    identification, the other the reverse. This uses the verified code AND
#    physical position, so it depends on neither.
#
# Reads evdev, not usbmon. A KB_* remap makes the MCU emit a standard HID
# keyboard usage which the generic parser maps one-to-one onto a keycode, so
# evdev answers both questions directly. usbmon works fine here; the Ally just
# reports on change, so an idle capture is empty rather than broken.
#
# InputPlumber is stopped for the duration and restarted afterwards; it holds
# these nodes with EVIOCGRAB and a grabbed node looks exactly like a dead
# button.
#
# btn_a and btn_m1 are restored on exit, including on Ctrl-C.
#
# Usage: sudo python3 kb_code_and_slot.py

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

KEYS = {66: "KEY_F8", 183: "KEY_F13", 184: "KEY_F14", 185: "KEY_F15",
        186: "KEY_F16", 187: "KEY_F17", 188: "KEY_F18", 190: "KEY_F20",
        148: "KEY_PROG1", 0x130: "BTN_SOUTH (A)", 0x131: "BTN_EAST (B)"}

# What each name should produce if the code table is correct.
EXPECT = {"KB_F8": 66, "KB_F14": 184, "KB_F15": 185}


def ally_nodes():
    out = {}
    for inp in sorted(glob.glob("/sys/class/input/input*")):
        try:
            name = open(os.path.join(inp, "name")).read().strip()
            phys = open(os.path.join(inp, "phys")).read().strip()
        except OSError:
            continue
        if not (("Ally" in name) or ("N-KEY" in name) or ("-2/input" in phys)):
            continue
        for ev in glob.glob(os.path.join(inp, "event*")):
            out["/dev/input/" + os.path.basename(ev)] = name
    return out


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


def flush(fds):
    for fd in fds:
        try:
            while select.select([fd], [], [], 0)[0]:
                if not os.read(fd, 65536):
                    break
        except (BlockingIOError, OSError):
            pass


def capture(fds, names, seconds, prompt):
    """Return keycodes seen pressed, in order of first appearance."""
    print(f"    >>> {prompt} ({seconds}s)...")
    flush(fds)
    seen = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r, _, _ = select.select(fds, [], [], max(0, end - time.monotonic()))
        for fd in r:
            try:
                data = os.read(fd, 65536)
            except (BlockingIOError, OSError):
                continue
            for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                _, _, et, code, val = struct.unpack(
                    EV_FORMAT, data[off:off + EV_SIZE])
                if et == EV_KEY and val == 1 and code not in seen:
                    seen.append(code)
                    print(f"        {names[fds.index(fd)]}: "
                          f"{KEYS.get(code, f'code {code}')}")
    if not seen:
        print("        nothing captured")
    return seen


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_nodes()
    a_p, m1_p = sysfs("btn_a/remap"), sysfs("btn_m1/remap")
    if not nodes or not a_p or not m1_p:
        sys.exit("Ally nodes or remap attributes missing - is our module loaded?")
    orig_a, orig_m1 = read_attr(a_p), read_attr(m1_p)
    print(f"btn_a  = {orig_a}\nbtn_m1 = {orig_m1}")

    ip = subprocess.run(["systemctl", "is-active", "--quiet",
                         "inputplumber"]).returncode == 0
    if ip:
        print("\nstopping inputplumber (it grabs these nodes)...")
        subprocess.run(["systemctl", "stop", "inputplumber"])
        time.sleep(2.0)

    fds, names, results = [], [], {}
    try:
        print("\nopening nodes:")
        for p in sorted(nodes):
            try:
                fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
                names.append(os.path.basename(p))
                print(f"  {p:<20} OK")
            except OSError as e:
                print(f"  {p:<20} FAILED: {e.strerror}")
        if not fds:
            sys.exit("Could not open any node.")

        input("\n[self-test] Enter, then press some buttons... ")
        if not capture(fds, names, 4, "press anything"):
            print("""
    Nothing on any node. Either InputPlumber did not release its grab, or the
    node set is wrong. Aborting rather than producing empty results that look
    like findings.""")
            return

        print("\n[1] CODE TABLE - write each name to btn_a, read what comes out")
        for name in ("KB_F8", "KB_F14", "KB_F15"):
            if not write_attr(a_p, name):
                continue
            time.sleep(0.4)
            input(f"\n  btn_a = {name}. Enter, then press A a few times... ")
            got = capture(fds, names, 5, "press the A button")
            want = EXPECT[name]
            results[name] = (got, want in got)
            print(f"        expected {KEYS[want]}: "
                  f"{'MATCH' if want in got else 'MISMATCH'}")
        write_attr(a_p, orig_a or "PAD_A")

        print("\n[2] PADDLE SLOT - btn_m1 only, identified by position")
        print("    Using KB_F8, whose code is verified correct on this device.")
        if write_attr(m1_p, "KB_F8"):
            time.sleep(0.4)
            input("\n  Enter, then press the LEFT back paddle... ")
            left = capture(fds, names, 5, "press the LEFT paddle")
            input("\n  Enter, then press the RIGHT back paddle... ")
            right = capture(fds, names, 5, "press the RIGHT paddle")
            results["slot"] = (66 in left, 66 in right)
        write_attr(m1_p, orig_m1 or "KB_M1")
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass
        print("\n[R] RESTORE")
        print(f"    btn_a  -> {read_attr(a_p)}")
        print(f"    btn_m1 -> {read_attr(m1_p)}")
        if ip:
            subprocess.run(["systemctl", "start", "inputplumber"])
            print("    inputplumber restarted")

    print("\nresult")
    print("-" * 62)
    for name in ("KB_F8", "KB_F14", "KB_F15"):
        if name in results:
            got, ok = results[name]
            shown = ", ".join(KEYS.get(c, str(c)) for c in got) or "nothing"
            print(f"  {name:<8} -> {shown:<30} {'ok' if ok else 'WRONG'}")
    if "slot" in results:
        left_hit, right_hit = results["slot"]
        side = ("LEFT" if left_hit and not right_hit else
                "RIGHT" if right_hit and not left_hit else None)
        if side:
            print(f"  btn_m1 drives the {side} paddle")
        else:
            print(f"  btn_m1 slot INCONCLUSIVE "
                  f"(left={left_hit}, right={right_hit})")

    print("""
How to read this:
  KB_F14 -> KEY_F14 and KB_F15 -> KEY_F15
      -> the table is correct on this model. PR #8's swap is specific to the
         Xbox Ally X and there is nothing here to pull.
  KB_F14 -> KEY_F15 and KB_F15 -> KEY_F14
      -> the swap is real here too, PR #8 applies, and every conclusion drawn
         from paddle_position.py inverts - including which paddle btn_m1
         drives, and therefore whether reverting the is_first swap was right.
  The slot line is independent of that: it uses KB_F8, already verified on this
  hardware, and identifies the paddle by hand rather than by a label printed
  where you cannot see it.
""")


if __name__ == "__main__":
    main()
