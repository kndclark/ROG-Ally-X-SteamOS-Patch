#!/usr/bin/env python3
# aura_c0_bc_probe.py - determine what 0xC0 ("Aura mode init") actually does.
#
# HHD's const.py groups the byte under "Init / turn off" and pairs it with a
# separate "Turn on (after init)" byte:
#
#     # Init / turn off
#     # 5a c0 00 01 <- this might fix some issues with leds being stuck
#     # Turn on (after init)
#     # 5a bc
#
# Nothing else documents either byte. Windows LightingService sends 0xC0 at
# init; HHD and OpenRGB both omit it and work. So the open question is whether
# 0xC0 initializes the Aura path or disables it - which decides whether adding
# it to our probe path is hardening or a way to ship dark rings.
#
# The test is visual and deliberately small: light the rings to a known bright
# white, send 0xC0, look, send 0xBC, look. No argument sweeps - blind sweeps of
# this command family are how the MCU host-path lock was hit before.
#
# Usage: sudo python3 aura_c0_bc_probe.py

import fcntl
import glob
import os
import sys
import time

REPORT_SIZE = 64
LED = "/sys/class/leds/go_s:rgb:joystick_rings"

# The driver's own MCU firmware-version query. Harmless, and the device answers
# only on the interface that serves the 0x5A command family - so it identifies
# the right node without writing anything that changes state.
FW_QUERY = [0x5A, 0x05, 0x03, 0x31, 0x00, 0x20]

AURA_INIT  = [0x5A, 0xC0, 0x00, 0x01]  # "Aura mode init"; acks with a status byte
AURA_INIT0 = [0x5A, 0xC0, 0x00, 0x00]  # same command, argument 0 - see probe A
AURA_BC    = [0x5A, 0xBC]              # HHD calls this "turn on"; it turns output OFF

# Hardware brightness, 4 levels 0-3. Confirmed in windows-led-capture.md ("5D BA
# C5 C4 <0|1|2|3>", Sessions B/D2) and built the same way by our driver at
# hid-asus.c asus_kbd_set_brightness(). Captures show 0x5D as the primary form
# with a 0x5A mirror, and our driver uses the 0x5A one, so probe B tries both.
BRIGHT_5A_MAX = [0x5A, 0xBA, 0xC5, 0xC4, 0x03]
BRIGHT_5D_MAX = [0x5D, 0xBA, 0xC5, 0xC4, 0x03]

# HHD's const.py documents a power-state bitmask we do not implement and had not
# recorded: "5a d1 09 01 <bits>" with 0f=all on, 00=all off, 09=boot/shutdown,
# 02=awake, 04=charging sleep. If this is the same subsystem as 0xBC, then 0xBC
# is not a new discovery. Only the permissive "all on" value is ever sent here -
# writing "all off" risks a persistent dark state, and its NV backing is untested.
PWR_ALL_ON = [0x5A, 0xD1, 0x09, 0x01, 0x0F]

GATE_SETTLE_S = 5


def ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


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


def ask(prompt):
    print()
    try:
        return input(f">>> {prompt} ").strip()
    except EOFError:
        print("(no tty - run this interactively)")
        sys.exit(1)


def find_node():
    """Return the hidraw node that answers the 0x5A firmware-version query."""
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        node = "/dev/" + path.split("/")[-1]
        try:
            uevent = open(path + "/device/uevent").read().upper()
        except OSError:
            continue
        if "0B05" not in uevent or "1B4C" not in uevent:
            continue
        try:
            fd = os.open(node, os.O_RDWR)
        except OSError:
            continue
        try:
            set_feature(fd, FW_QUERY)
            resp = get_feature(fd)
            # A real answer carries the ASCII version string, e.g. RC72LA.315
            if b"RC" in resp or b"FGA" in resp:
                print(f"  {node}: answers fw query -> {resp[:24].hex(' ')}")
                ascii_part = "".join(chr(c) for c in resp if 32 <= c < 127)
                print(f"    ascii: {ascii_part.strip()}")
                return fd, node
        except OSError:
            pass
        os.close(fd)
    return None, None


def show(fd, label):
    resp = get_feature(fd)
    print(f"    readback after {label}: {resp[:8].hex(' ')}")
    return resp


def status_byte(resp):
    """Offset 4 of the readback.

    We zero-pad every packet we send, so a non-zero byte here was written by
    the device, not echoed from us. For 0x5A C0 00 01 the device returns 01,
    matching the Windows capture.
    """
    return resp[4]


def main():
    if os.geteuid() != 0:
        print("needs root for hidraw feature reports: sudo python3 aura_c0_bc_probe.py")
        sys.exit(1)

    if not os.path.isdir(LED):
        print(f"LED class dir not found: {LED}")
        print("Is our driver loaded? (ls /sys/class/leds | grep joystick)")
        sys.exit(1)

    print("== locating the interface that serves the 0x5A command family ==")
    fd, node = find_node()
    if not fd:
        print("No Ally interface answered the firmware-version query. Aborting.")
        sys.exit(1)

    saved = {a: read_sysfs(a) for a in ("effect", "multi_intensity", "brightness")}
    print(f"\nsaved current LED state: {saved}")

    try:
        print("\n== step 0: baseline - rings to bright white ==")
        write_sysfs("effect", "monocolor")
        write_sysfs("multi_intensity", "255 255 255")
        write_sysfs("brightness", "100")
        r = ask("Are BOTH joystick rings lit solid white? (y/n)")
        if not r.lower().startswith("y"):
            print("Baseline not established - stopping before sending anything.")
            return

        print("\n== step 1: send 5A C0 00 01 ==")
        print(f"    writing: {' '.join(f'{b:02x}' for b in AURA_INIT)}")
        set_feature(fd, AURA_INIT)
        resp1 = show(fd, "0xC0")
        print("    (waiting for your observation - the rings are the experiment)")
        obs1 = ask("What happened to the rings? [off / unchanged / other - describe]")

        print("\n== probe A: is the 0xC0 status byte a state, or an echo? ==")
        print("    Resend the same command with argument 0 and compare offset 4.")
        print(f"    writing: {' '.join(f'{b:02x}' for b in AURA_INIT0)}")
        set_feature(fd, AURA_INIT0)
        resp0 = show(fd, "0xC0 arg=00")
        st1, st0 = status_byte(resp1), status_byte(resp0)
        print(f"    status byte: arg=01 -> 0x{st1:02x}    arg=00 -> 0x{st0:02x}")
        if st1 == st0:
            probe_a = (f"CONSTANT 0x{st1:02x} - device-generated, not an argument echo. "
                       f"Fixed ack vs live state is still undetermined.")
        else:
            probe_a = f"TRACKS ARGUMENT (0x{st1:02x} vs 0x{st0:02x}) - just an echo, no information"
        print(f"    -> {probe_a}")
        # Put the argument back the way Windows leaves it.
        set_feature(fd, AURA_INIT)

        print("\n== step 2: send 5A BC ==")
        print(f"    writing: {' '.join(f'{b:02x}' for b in AURA_BC)}")
        set_feature(fd, AURA_BC)
        show(fd, "0xBC")
        obs2 = ask("What happened now? [came back on / still off / unchanged - describe]")

        print("\n== probe C1: read the 0xC0 status byte WHILE the gate is active ==")
        print("    A register tracking OUTPUT-ENABLE should differ here. One tracking")
        print("    path health would not - the path is fine, only output is blanked.")
        print("    Sent before anything else so the reading stays uncontaminated.")
        set_feature(fd, AURA_INIT)
        respg = show(fd, "0xC0 while gated")
        stg = status_byte(respg)
        print(f"    status byte: rings lit -> 0x{st1:02x}    gated -> 0x{stg:02x}")
        if stg != st1:
            probe_c1 = (f"CHANGED 0x{st1:02x} -> 0x{stg:02x} - live register tracking output "
                        f"state. Whether it also reports a LOCKED path is a separate "
                        f"question needing a locked unit.")
        else:
            probe_c1 = (f"UNCHANGED 0x{stg:02x} - does not track output-enable. Consistent "
                        f"with EITHER a fixed ack OR a health register that reads 1 on any "
                        f"working unit; undetermined without a locked unit.")
        print(f"    -> {probe_c1}")

        print(f"\n== control: does the gate clear on its own? waiting {GATE_SETTLE_S}s ==")
        print("    No writes during this window. If the rings return by themselves the")
        print("    gate is time-limited, and 'the config write cleared it' is wrong.")
        time.sleep(GATE_SETTLE_S)
        obs_t = ask(f"After {GATE_SETTLE_S}s with no writes, are the rings still dark? (y/n)")
        ctrl_timer = ("gate held - not a timer" if obs_t.lower().startswith("y")
                      else "GATE SELF-CLEARED - it is time-limited, earlier reading is WRONG")
        print(f"    -> {ctrl_timer}")

        print("\n== probe B: did 0xBC zero the hardware brightness, or gate output? ==")
        drv_br = read_sysfs("brightness")
        print(f"    driver-side brightness still reads: {drv_br}")
        print("    Sending brightness=max as a RAW packet - no config write, so if the")
        print("    rings return, 0xBC only moved the brightness level.")
        print(f"    writing: {' '.join(f'{b:02x}' for b in BRIGHT_5A_MAX)}")
        set_feature(fd, BRIGHT_5A_MAX)
        obs_b1 = ask("Did the rings come back? (y/n)")
        if obs_b1.lower().startswith("y"):
            probe_b = f"brightness path (0x5A form restored it; driver read {drv_br})"
        else:
            print(f"    still dark - trying the 0x5D form the captures call primary")
            print(f"    writing: {' '.join(f'{b:02x}' for b in BRIGHT_5D_MAX)}")
            set_feature(fd, BRIGHT_5D_MAX)
            obs_b2 = ask("Did the rings come back now? (y/n)")
            if obs_b2.lower().startswith("y"):
                probe_b = f"brightness path, but only via 0x5D (driver read {drv_br})"
            else:
                probe_b = (f"NOT brightness - 0xBC is a separate output gate "
                           f"(driver read {drv_br}, both brightness forms failed)")
        print(f"    -> {probe_b}")

        print("\n== probe C2 (OPTIONAL): is 0xBC just the known D1-09 power-state? ==")
        print("    HHD documents 5A D1 09 01 <bits> as an LED power-state bitmask")
        print("    (0f=all on). We neither implement nor had documented it. If sending")
        print("    'all on' clears the 0xBC gate, the two are the same subsystem and")
        print("    0xBC is not a new find.")
        print("    CAUTION: this writes a power-state whose NV backing is untested.")
        print("    Only the permissive 'all on' value is sent - never 'all off'.")
        if ask("Run this control? (y/n)").lower().startswith("y"):
            print(f"    writing: {' '.join(f'{b:02x}' for b in PWR_ALL_ON)}")
            set_feature(fd, PWR_ALL_ON)
            r2 = ask("Did the rings come back? (y/n)")
            probe_c2 = ("SAME SUBSYSTEM - D1-09 'all on' cleared the 0xBC gate"
                        if r2.lower().startswith("y")
                        else "DISTINCT - D1-09 'all on' did not clear the 0xBC gate")
        else:
            probe_c2 = "skipped"
        print(f"    -> {probe_c2}")

        print("\n== step 3: does the driver still control the LEDs? ==")
        write_sysfs("multi_intensity", "255 0 0")
        obs3 = ask("Did the rings turn RED? (y/n - 'n' means the Aura path is gated off)")

        print("\n" + "=" * 60)
        print("RESULT")
        print("=" * 60)
        print(f"  after 0xC0            : {obs1}")
        print(f"  after 0xBC            : {obs2}")
        print(f"  sysfs still works     : {obs3}")
        print(f"  probe A (0xC0 status) : {probe_a}")
        print(f"  probe B (0xBC nature) : {probe_b}")
        print(f"  probe C1 (gated 0xC0) : {probe_c1}")
        print(f"  control (self-clear)  : {ctrl_timer}")
        print(f"  probe C2 (vs D1-09)   : {probe_c2}")
        print()
        print("Reading (established 2026-08-23: 0xC0 is inert, 0xBC turns output off):")
        print("  0xC0 unchanged + 0xBC off + sysfs works")
        print("      -> reproduces the known result. 0xC0 stays safe for the probe path;")
        print("         0xBC must never be sent by the driver.")
        print("  probe A/C1 both CONSTANT 0x01")
        print("      -> the byte is invariant across argument AND output-gated state.")
        print("         Note the 0xBC gate is NOT a broken path - the very next config")
        print("         write lights the rings - so a 'path healthy' register would also")
        print("         read 1 here. This narrows 0xC0 but does not settle it; the")
        print("         decisive read still needs a LOCKED unit.")
        print("  probe A TRACKS ARGUMENT")
        print("      -> the byte is a plain echo. 0xC0 exposes nothing this way;")
        print("         binary analysis of LightingService is then the only route left.")
        print("  probe B brightness path")
        print("      -> 0xBC is just the D2 brightness-to-zero path under another name.")
        print("  probe B NOT brightness")
        print("      -> 0xBC is a distinct output gate that leaves config intact,")
        print("         which is the standby/lights-off primitive we hypothesised.")
        print("  anything ELSE (0xC0 turns rings off, or nothing recovers)")
        print("      -> unexpected; stop and report before running it again.")

    finally:
        print("\n== restoring your saved LED state ==")
        # MEASURED 2026-08-23: 0xBC is what turns the rings OFF - 0xC0 had no
        # visible effect. So sending 0xBC here would darken them, not recover
        # them. A plain sysfs write is what actually restores output.
        for attr in ("effect", "multi_intensity", "brightness"):
            if saved.get(attr) not in (None, "?"):
                write_sysfs(attr, saved[attr])
        print(f"    restored: {[read_sysfs(a) for a in ('effect','multi_intensity','brightness')]}")
        os.close(fd)
        print("\nIf the rings are still dark, power-cycle first; if that fails see")
        print("investigations/led-mcu-host-path-lock.md before doing anything else.")


if __name__ == "__main__":
    main()
