#!/usr/bin/env python3
# accc_timeline.py - what do the AC and CC buttons actually do, on the wire and
# in evdev, with timing?
#
# READ-ONLY on the device: sends no packets, writes no sysfs. It does stop
# InputPlumber for the duration (and restarts it), because InputPlumber holds
# the evdev nodes with EVIOCGRAB and a grabbed node is indistinguishable from a
# dead button.
#
# Why this exists
# ---------------
# Two symptoms need explaining:
#   1. CC registers only occasionally in Steam's controller test screen, yet
#      reliably opens the Steam menu outside it.
#   2. Steam Deck style chords (Steam+X for the keyboard, Steam+B to close a
#      game) do not work on the Ally X.
#
# Both are consistent with how hid-asus emits these keys today:
#
#     input_report_key(keyboard_input, keycode, 1);   /* press   */
#     input_sync(keyboard_input);
#     input_report_key(keyboard_input, keycode, 0);   /* release */
#     input_sync(keyboard_input);
#
# That is an instantaneous tap - the key is never held, so a polling UI misses
# it and a chord can never form. The upstream hid-asus-ally instead does:
#
#     input_report_key(input, KEY_F16, data[1] == 0xA6);
#
# where the VALUE is the comparison, so the key stays down while its byte keeps
# arriving and releases when a different byte does.
#
# Which design is correct depends entirely on what the MCU actually puts on the
# wire, and nobody has measured that on the Ally X. Nero's account of the
# original Ally - "fires both press and release at release" - suggests the
# device may not report a press edge at all, in which case holding is
# impossible and the tap is unavoidable. This measures it.
#
# What it records
# ---------------
# Every vendor report on the config interface (hidraw, 1.2) and every EV_KEY
# event on every Ally evdev node, merged into one timeline with millisecond
# offsets and the evdev VALUE (0=release, 1=press, 2=autorepeat). Earlier tools
# in this directory recorded only value==1, which is exactly the information
# needed here and exactly what they threw away.
#
# Usage: sudo python3 accc_timeline.py

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

KEYS = {
    148: "KEY_PROG1", 183: "KEY_F13", 184: "KEY_F14", 185: "KEY_F15",
    186: "KEY_F16", 187: "KEY_F17", 188: "KEY_F18", 189: "KEY_F19",
    190: "KEY_F20", 29: "KEY_LEFTCTRL", 56: "KEY_LEFTALT", 111: "KEY_DELETE",
    0x13c: "BTN_MODE (Guide)", 0x130: "BTN_SOUTH (A)", 0x131: "BTN_EAST (B)",
}
VALUES = {0: "RELEASE", 1: "PRESS", 2: "REPEAT"}

# Vendor bytes seen on the 0x5A config reports.
VENDOR = {
    0x38: "AC short press", 0x93: "AC (Ally X variant)",
    0xA5: "paddle", 0xA6: "CC / QAM", 0xA7: "long press",
    0xA8: "long press RELEASE", 0x00: "release (generic)",
}

STEPS = [
    ("AC tap", "tap the AC button once, quickly", 6),
    ("AC hold 3s", "press AC and HOLD for ~3s, then release", 8),
    ("CC tap", "tap the CC button once, quickly", 6),
    ("CC hold 3s", "press CC and HOLD for ~3s, then release", 8),
    ("CC rapid x3", "tap CC three times as fast as you can", 6),
    ("CC held + A", "hold CC down, press A twice, then release CC", 8),
]


def ally_nodes():
    """Every Ally evdev node, resolved by name (they renumber on reload)."""
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


def cfg_hidraw():
    """hidraw node for interface 1.2, where the 0x5A vendor reports arrive."""
    for hr in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        path = os.path.realpath(hr)
        while path != "/":
            f = os.path.join(path, "bInterfaceNumber")
            if os.path.exists(f):
                try:
                    num = int(open(f).read().strip(), 16)
                    parent = os.path.dirname(path)
                    vid = open(os.path.join(parent, "idVendor")).read().strip()
                    pid = open(os.path.join(parent, "idProduct")).read().strip()
                except OSError:
                    break
                if vid == "0b05" and pid in ("1b4c", "1abe") and num == 2:
                    return "/dev/" + os.path.basename(hr)
                break
            path = os.path.dirname(path)
    return None


def holders(path):
    found = []
    for pid in glob.glob("/proc/[0-9]*"):
        try:
            for fd in glob.glob(os.path.join(pid, "fd", "*")):
                if os.path.realpath(fd) == path:
                    found.append(open(os.path.join(pid, "comm")).read().strip())
                    break
        except (OSError, PermissionError):
            continue
    return found


def check_binding():
    print("driver binding per interface:")
    ok = True
    for d in sorted(glob.glob("/sys/bus/hid/devices/*1B4C*")):
        drv = os.path.basename(os.path.realpath(os.path.join(d, "driver")))
        print(f"  {os.path.basename(d)}  -> {drv}")
        if drv not in ("asus", "asus_rog_ally"):
            ok = False
    if not ok:
        print("  WARNING: unexpected driver bound; results may not reflect our module")
    print()


def capture(fds, labels, seconds, prompt):
    print(f"\n--- {prompt} ---")
    print(f"    recording {seconds}s, go...")
    events = []
    t0 = time.monotonic()
    deadline = t0 + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select(fds, [], [], max(0, deadline - time.monotonic()))
        now = (time.monotonic() - t0) * 1000.0
        for fd in ready:
            label = labels[fds.index(fd)]
            try:
                data = os.read(fd, 4096)
            except (BlockingIOError, OSError):
                continue
            if not data:
                continue
            if label == "hidraw":
                if data[0] == 0x5A and len(data) > 1:
                    note = VENDOR.get(data[1], "")
                    hexs = " ".join(f"{b:02x}" for b in data[:6])
                    events.append((now, "WIRE 1.2", f"{hexs}   {note}"))
            else:
                for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                    _, _, et, code, val = struct.unpack(
                        EV_FORMAT, data[off:off + EV_SIZE])
                    if et == EV_KEY:
                        events.append((now, f"evdev {label}",
                                       f"{KEYS.get(code, f'code {code}')} "
                                       f"{VALUES.get(val, val)}"))
    if not events:
        print("      (nothing captured)")
        return events
    for t, src, what in events:
        print(f"      {t:8.1f} ms  {src:<16} {what}")
    return events


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    check_binding()

    nodes = ally_nodes()
    cfg = cfg_hidraw()
    if not nodes:
        sys.exit("No Ally evdev nodes found.")
    print(f"config hidraw : {cfg or 'NOT FOUND'}")
    print("evdev nodes:")
    for p, n in sorted(nodes.items()):
        h = holders(p)
        print(f"  {os.path.basename(p):<10} {n:<38} {'held by ' + ','.join(h) if h else 'free'}")

    ip = subprocess.run(["systemctl", "is-active", "--quiet",
                         "inputplumber"]).returncode == 0
    if ip:
        print("\nstopping inputplumber (it grabs these nodes)...")
        subprocess.run(["systemctl", "stop", "inputplumber"])
        time.sleep(2.0)
        still = [os.path.basename(p) for p in nodes if holders(p)]
        print(f"  still held after stop: {still or 'none'}")

    fds, labels = [], []
    try:
        print("\nopening channels:")
        if cfg:
            try:
                fds.append(os.open(cfg, os.O_RDONLY | os.O_NONBLOCK))
                labels.append("hidraw")
                print(f"  {cfg:<18} OK")
            except OSError as e:
                print(f"  {cfg:<18} FAILED: {e.strerror}")
        for p in sorted(nodes):
            try:
                fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
                labels.append(os.path.basename(p))
                print(f"  {p:<18} OK")
            except OSError as e:
                print(f"  {p:<18} FAILED: {e.strerror}")
        if not fds:
            sys.exit("Could not open any channel.")

        print("\nPress Enter before each step, then perform ONLY that action.")
        for name, prompt, secs in STEPS:
            input(f"\n[{name}] Enter when ready... ")
            for fd in fds:
                try:
                    os.read(fd, 65536)
                except (BlockingIOError, OSError):
                    pass
            capture(fds, labels, secs, prompt)
    finally:
        for fd in fds:
            os.close(fd)
        if ip:
            subprocess.run(["systemctl", "start", "inputplumber"])
            print("\ninputplumber restarted")

    print("""
What to look for:
  A WIRE line for press AND a separate one for release
      -> the MCU reports both edges; a held key is achievable and our
         instantaneous tap is simply wrong.
  Only ONE WIRE line, appearing when you RELEASE
      -> the MCU reports a single edge at release, matching Nero's account of
         the original Ally. Holding is impossible from the wire, and any fix
         has to synthesise the hold with a timer.
  evdev PRESS and RELEASE in the same millisecond
      -> confirms the tap, and explains both the test-screen flakiness and the
         chords not working.
  'CC held + A': whether CC is still held (no RELEASE yet) when A arrives
      -> this is the chord question, measured directly.
""")


if __name__ == "__main__":
    main()
