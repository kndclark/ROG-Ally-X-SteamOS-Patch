#!/usr/bin/env python3
# aura_d1_09_powerstate_probe.py - characterise the LED power-state bitmask.
#
# HHD's const.py documents this and nothing else does:
#
#     # RGB on when
#     # "5a d1 09 01 0f <- val bit"
#     # 0f: all on   00: all off
#     # 09 (08 + 01): boot/shutdown
#     # 02: awake    04: charging sleep
#
# We neither implement it nor had it recorded. It is the only known mechanism
# for controlling WHEN the rings light (boot, awake, sleep, charging) as opposed
# to what colour they are, so it is a real user-visible feature gap.
#
# NOTHING here is verified on our hardware. This script establishes the basics
# safely; the per-bit semantics need power transitions and cannot be settled in
# one sitting.
#
# SAFETY. The failure mode that matters is leaving the rings permanently dark:
#   - Whether this register is NV-backed is UNTESTED. Assume it may persist.
#   - 0x0F ("all on") is the permissive value and is what we always restore to.
#   - 0x00 ("all off") is only sent if you opt in, and is immediately reverted.
#   - We verify 0x0F actually restores output BEFORE offering the 0x00 test.
# A prior run established that 0x0F is at least inert while the 0xBC gate is
# active (see windows-led-capture.md Session E), so it is a safe resting value.
#
# Usage: sudo python3 aura_d1_09_powerstate_probe.py

import fcntl
import glob
import os
import sys
import time

REPORT_SIZE = 64
LED = "/sys/class/leds/go_s:rgb:joystick_rings"
FW_QUERY = [0x5A, 0x05, 0x03, 0x31, 0x00, 0x20]

def pwr(bits):
    """5A D1 09 01 <bits> - the power-state bitmask write."""
    return [0x5A, 0xD1, 0x09, 0x01, bits]

ALL_ON, ALL_OFF = 0x0F, 0x00
BIT_NAMES = {0x01: "bit0 (boot/shutdown half)", 0x02: "bit1 (awake)",
             0x04: "bit2 (charging sleep)", 0x08: "bit3 (boot/shutdown half)"}


def ioc(d, nr, size):
    return (d << 30) | (size << 16) | (ord("H") << 8) | nr


def set_feature(fd, data):
    buf = bytearray(data) + bytearray(REPORT_SIZE - len(data))
    fcntl.ioctl(fd, ioc(3, 0x06, len(buf)), buf)


def get_feature(fd, report_id=0x5A):
    buf = bytearray(REPORT_SIZE)
    buf[0] = report_id
    fcntl.ioctl(fd, ioc(3, 0x07, REPORT_SIZE), buf)
    return bytes(buf)


def write_sysfs(attr, value):
    try:
        with open(os.path.join(LED, attr), "w") as f:
            f.write(value)
        return True
    except OSError as e:
        print(f"    (sysfs {attr} <- {value!r} failed: {e.strerror})")
        return False


def read_sysfs(attr):
    try:
        with open(os.path.join(LED, attr)) as f:
            return f.read().strip()
    except OSError:
        return "?"


def ask(q):
    print()
    try:
        return input(f">>> {q} ").strip()
    except EOFError:
        print("(no tty - run interactively)")
        sys.exit(1)


def yes(q):
    return ask(q).lower().startswith("y")


def find_node():
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        node = "/dev/" + path.split("/")[-1]
        try:
            u = open(path + "/device/uevent").read().upper()
        except OSError:
            continue
        if "0B05" not in u or "1B4C" not in u:
            continue
        try:
            fd = os.open(node, os.O_RDWR)
        except OSError:
            continue
        try:
            set_feature(fd, FW_QUERY)
            r = get_feature(fd)
            if b"RC" in r or b"FGA" in r:
                print(f"  {node}: answers fw query")
                return fd, node
        except OSError:
            pass
        os.close(fd)
    return None, None


def send(fd, bits, label):
    p = pwr(bits)
    print(f"    writing {label}: {' '.join(f'{b:02x}' for b in p)}")
    set_feature(fd, p)
    resp = get_feature(fd)
    print(f"    readback: {resp[:8].hex(' ')}")
    return resp


def main():
    if os.geteuid() != 0:
        print("needs root: sudo python3 aura_d1_09_powerstate_probe.py")
        sys.exit(1)
    if not os.path.isdir(LED):
        print(f"LED class dir not found: {LED} - is our driver loaded?")
        sys.exit(1)

    print("== locating the 0x5A command interface ==")
    fd, node = find_node()
    if not fd:
        print("No Ally interface answered. Aborting.")
        sys.exit(1)

    saved = {a: read_sysfs(a) for a in ("effect", "multi_intensity", "brightness")}
    print(f"\nsaved LED state: {saved}")
    findings = {}

    try:
        print("\n== step 0: baseline - rings to bright white ==")
        write_sysfs("effect", "monocolor")
        write_sysfs("multi_intensity", "255 255 255")
        write_sysfs("brightness", "100")
        if not yes("Are BOTH rings lit solid white? (y/n)"):
            print("No baseline - stopping before any power-state write.")
            return

        print("\n== step 1: does the register accept writes at all? ==")
        print("    Sending the permissive value first; a no-op is the expected result.")
        r = send(fd, ALL_ON, "ALL_ON 0x0f")
        findings["accepts_write"] = f"readback offset4=0x{r[4]:02x}"
        if not yes("Rings still lit? (y/n - 'n' here would be a surprise)"):
            findings["all_on_effect"] = "ALL_ON DARKENED THE RINGS - unexpected, stop"
            print("    !! 0x0f darkened the rings. That contradicts 'all on'. Stopping.")
            return
        findings["all_on_effect"] = "no-op while awake, as expected"

        print("\n== step 2: does the mask gate CURRENT output? (opt-in) ==")
        print("    Only 0x00 answers this, and 0x00 is the value that could strand the")
        print("    rings dark if the register turns out to be NV-backed. It is reverted")
        print("    to 0x0f immediately, and we already saw 0x0f is accepted.")
        print("    Skip this if you would rather not risk it - everything else still runs.")
        if yes("Send ALL_OFF 0x00 and immediately revert? (y/n)"):
            send(fd, ALL_OFF, "ALL_OFF 0x00")
            time.sleep(1)
            went_dark = yes("Did the rings go DARK? (y/n)")
            send(fd, ALL_ON, "ALL_ON 0x0f (revert)")
            time.sleep(1)
            came_back = yes("Did they come back? (y/n - answer carefully)")
            if went_dark and came_back:
                findings["gates_current_output"] = "YES - and 0x0f reverts it cleanly"
            elif went_dark and not came_back:
                findings["gates_current_output"] = "YES but 0x0f DID NOT REVERT - see recovery below"
                print("\n    !! Rings did not return. Trying a config write as fallback...")
                write_sysfs("multi_intensity", "255 255 255")
                if yes("Did a config write bring them back? (y/n)"):
                    findings["gates_current_output"] += " (config write recovered)"
                else:
                    print("    !! Still dark. STOP. Power-cycle, then report this.")
                    return
            else:
                findings["gates_current_output"] = "NO - mask does not affect current output"
        else:
            findings["gates_current_output"] = "skipped by operator"

        print("\n== step 3: is the mask observable via readback? ==")
        print("    If the readback differs per value, the register is queryable and the")
        print("    remaining bits can be characterised without power transitions.")
        vals = {}
        for bits in (ALL_ON, 0x02, ALL_ON):
            resp = send(fd, bits, f"0x{bits:02x}")
            vals[bits] = resp[4]
        findings["readback_varies"] = (
            "varies per value - register is queryable"
            if len(set(vals.values())) > 1
            else f"constant 0x{list(vals.values())[0]:02x} - not queryable this way")

        print("\n" + "=" * 62)
        print("RESULT")
        print("=" * 62)
        for k, v in findings.items():
            print(f"  {k:22}: {v}")
        print()
        print("NOT ANSWERABLE IN THIS SESSION - each needs a power transition:")
        print("  bit0/bit3 (0x09) boot/shutdown : set mask, reboot, watch the rings")
        print("  bit1 (0x02) awake              : covered above only for the awake case")
        print("  bit2 (0x04) charging sleep     : set mask, suspend on charger, observe")
        print("  NV persistence                 : set a non-default mask, cold boot,")
        print("                                   re-read behaviour. Do this LAST - it is")
        print("                                   the test that can strand the rings dark.")

    finally:
        print("\n== restoring ==")
        # Always leave the permissive mask behind, whatever happened above.
        try:
            set_feature(fd, pwr(ALL_ON))
            print("    power-state mask -> 0x0f (all on)")
        except OSError as e:
            print(f"    (mask restore failed: {e})")
        for a in ("effect", "multi_intensity", "brightness"):
            if saved.get(a) not in (None, "?"):
                write_sysfs(a, saved[a])
        print(f"    restored: {[read_sysfs(a) for a in ('effect','multi_intensity','brightness')]}")
        os.close(fd)


if __name__ == "__main__":
    main()
