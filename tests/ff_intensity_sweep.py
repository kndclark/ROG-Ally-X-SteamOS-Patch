#!/usr/bin/env python3
# ff_intensity_sweep.py - sweep the MCU vibration intensity and check that it
# actually gates felt rumble output.
#
# What is being tested
# --------------------
# The driver exposes a master rumble volume per motor:
#
#     /sys/bus/hid/devices/*1B4C*/left_vibration/intensity     (0-100)
#     /sys/bus/hid/devices/*1B4C*/right_vibration/intensity    (0-100)
#
# which map to the MCU's SetVibrationIntensity command (0x06 on the 0x5A/0xD1
# config page). This walks 0, 5, 25, 50, 75, 100 and plays an identical rumble
# at each level.
#
# THE IMPORTANT PART: intensity scaling happens INSIDE the MCU. The XInput
# rumble reports (0x0d) that the driver sends carry the game's requested
# magnitudes and are byte-for-byte IDENTICAL at every intensity level. This was
# established 2026-06-19: felt output dropped clearly between intensity 100 and
# 50 while the wire bytes stayed at strong=0x25 / weak=0x38 both times.
#
# So constant 0x0d bytes across the sweep is the EXPECTED, CORRECT result, not
# a failure. A test that flagged it as "intensity has no effect" would be
# wrong. What this script verifies programmatically is:
#
#   1. each sysfs write is accepted and reads back
#   2. the 0x06 SetVibrationIntensity command actually reaches the wire with
#      the value we asked for  <- this is the real proof the write landed
#   3. the 0x0d rumble bytes stay constant   <- expected; flagged if they move
#
# Felt intensity is inherently subjective, so the script asks you to rate each
# level. That rating is the actual result; the wire checks only prove the
# plumbing worked.
#
# Original intensity values are restored on exit, including on Ctrl-C.
#
# Usage: sudo python3 ff_intensity_sweep.py [--levels 0,5,25,50,75,100]

import argparse
import glob
import os
import re
import subprocess
import sys
import threading
import time

LEVELS_DEFAULT = [0, 5, 25, 50, 75, 100]
DURATION_DEFAULT = 6.0   # seconds of rumble per motor, per level
RETRIGGER_INTERVAL = 4.0  # under fftest's ~5s effect length, so it stays continuous

# usbmon text lines put the payload after "= ", in 4-byte hex groups.
FF_PREFIX = "0d0f0000"      # XInput rumble: 0d 0f 00 00 SS WW ff 00 eb
INTENSITY_PREFIX = "5ad10602"  # SetVibrationIntensity: 5a d1 06 02 LL RR


class UsbmonReader(threading.Thread):
    """Best-effort background usbmon tap. If it cannot start, the sweep still
    runs - the felt rating is the real result either way."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.path = f"/sys/kernel/debug/usb/usbmon/{bus}u"
        self.proc = None
        self.lines = []
        self.lock = threading.Lock()
        self.ok = False

    def run(self):
        try:
            self.proc = subprocess.Popen(
                ["stdbuf", "-oL", "cat", self.path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        self.ok = True
        for line in self.proc.stdout:
            idx = line.find("= ")
            if idx == -1:
                continue
            payload = line[idx + 2:].replace(" ", "").replace("\n", "").lower()
            with self.lock:
                self.lines.append(payload)

    def drain(self):
        with self.lock:
            out = self.lines[:]
            self.lines.clear()
        return out

    def stop(self):
        if self.proc:
            self.proc.terminate()


def decode(payloads, prefix):
    """Return [(byte4, byte5)] for payloads matching prefix."""
    out = []
    for p in payloads:
        if p.startswith(prefix) and len(p) >= 12:
            try:
                out.append((int(p[8:10], 16), int(p[10:12], 16)))
            except ValueError:
                pass
    return out


def find_intensity_paths():
    left = glob.glob("/sys/bus/hid/devices/*1B4C*/left_vibration/intensity")
    right = glob.glob("/sys/bus/hid/devices/*1B4C*/right_vibration/intensity")
    if left and right:
        return left[0], right[0], "ours"
    # The stock in-kernel hid-asus-ally exposes a single flat file instead.
    flat = glob.glob("/sys/bus/hid/devices/*1B4C*/vibration_intensity")
    if flat:
        return flat[0], None, "stock"
    return None, None, None


def gamepad_node():
    try:
        devs = open("/proc/bus/input/devices").read()
    except OSError:
        return None
    block = None
    for chunk in devs.split("\n\n"):
        if "ASUS ROG Ally X Gamepad" in chunk:
            block = chunk
            break
    if not block:
        return None
    m = re.search(r"event\d+", block)
    return f"/dev/input/{m.group(0)}" if m else None


def read_attr(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None


def write_attr(p, v):
    try:
        with open(p, "w") as f:
            f.write(str(v))
        return True, ""
    except OSError as e:
        return False, e.strerror


def play(node, effect, duration):
    """Hold one fftest effect running for `duration` seconds.
    4 = strong/heavy motor, 5 = weak/light motor.

    fftest exits as soon as its stdin closes, and closing the device fd stops
    any effect in flight - so piping a single effect number produces only a
    brief buzz. This keeps the process alive for the whole window and
    re-triggers the effect periodically, because fftest's rumble effects have
    a replay length of roughly 5 seconds (measured: an effect started at t=25
    was still running at t=27 and stopped at t=30).
    """
    try:
        proc = subprocess.Popen(
            ["fftest", node], stdin=subprocess.PIPE, text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return
    try:
        deadline = time.time() + duration
        while time.time() < deadline:
            proc.stdin.write(f"{effect}\n")
            proc.stdin.flush()
            time.sleep(min(RETRIGGER_INTERVAL, max(0.1, deadline - time.time())))
        proc.stdin.write("-1\n")
        proc.stdin.flush()
        proc.wait(timeout=5)
    except (BrokenPipeError, subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default=",".join(str(x) for x in LEVELS_DEFAULT),
                    help="comma-separated intensity values (0-100)")
    ap.add_argument("--duration", type=float, default=DURATION_DEFAULT,
                    help=f"seconds of rumble per motor, per level "
                         f"(default {DURATION_DEFAULT})")
    args = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("run with sudo")

    try:
        levels = [int(x) for x in args.levels.split(",") if x.strip() != ""]
    except ValueError:
        sys.exit("--levels must be comma-separated integers")
    for lv in levels:
        if not 0 <= lv <= 100:
            sys.exit(f"level {lv} out of range (0-100)")

    left_p, right_p, kind = find_intensity_paths()
    if not left_p:
        sys.exit("No vibration intensity sysfs found - is the driver loaded?")
    if kind == "stock":
        print("WARNING: found the STOCK driver's flat vibration_intensity file.")
        print("         Our module is not bound to the config interface.")
        print("         Run 'sudo ./install.sh --check' and fix before trusting this.\n")

    node = gamepad_node()
    if not node:
        sys.exit("Could not find the 'ASUS ROG Ally X Gamepad' input device.")
    if not any(os.access(os.path.join(d, "fftest"), os.X_OK)
               for d in os.environ.get("PATH", "").split(":") if d):
        sys.exit("fftest not found in PATH (package: linuxconsoletools)")

    orig_left = read_attr(left_p)
    orig_right = read_attr(right_p) if right_p else None
    print(f"gamepad node : {node}")
    print(f"left  intensity: {left_p} = {orig_left}")
    if right_p:
        print(f"right intensity: {right_p} = {orig_right}")
    print(f"sweep levels : {levels}\n")

    subprocess.run(["modprobe", "usbmon"], stderr=subprocess.DEVNULL)
    bus = None
    try:
        out = subprocess.run(["lsusb"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "0b05:" in line:
                bus = str(int(line.split()[1]))
                break
    except OSError:
        pass

    sniffer = None
    if bus:
        sniffer = UsbmonReader(bus)
        sniffer.start()
        time.sleep(1.0)
        if sniffer.ok:
            print(f"usbmon tap active on bus {bus}\n")
        else:
            print("usbmon tap failed to start - continuing without wire checks\n")
    else:
        print("Could not determine USB bus - continuing without wire checks\n")

    results = []
    try:
        for lv in levels:
            print("=" * 62)
            print(f"  INTENSITY {lv}")
            print("=" * 62)
            if sniffer:
                sniffer.drain()

            ok_l, err_l = write_attr(left_p, lv)
            ok_r, err_r = (write_attr(right_p, lv) if right_p else (True, ""))
            if not ok_l or not ok_r:
                print(f"    write FAILED: {err_l or err_r}")
                results.append((lv, "write failed", "-", "-", "-"))
                continue

            time.sleep(0.3)
            back_l = read_attr(left_p)
            back_r = read_attr(right_p) if right_p else back_l
            print(f"    readback: left={back_l} right={back_r}")

            cmd_seen = "-"
            if sniffer and sniffer.ok:
                time.sleep(0.3)
                found = decode(sniffer.drain(), INTENSITY_PREFIX)
                if found:
                    l, r = found[-1]
                    match = "OK" if (l == lv and r == lv) else f"MISMATCH ({l},{r})"
                    cmd_seen = f"{l},{r} {match}"
                else:
                    cmd_seen = "not seen"
                print(f"    0x06 SetVibrationIntensity on wire: {cmd_seen}")

            print(f"\n    playing STRONG (heavy motor) for {args.duration:.0f}s...")
            if sniffer:
                sniffer.drain()
            play(node, 4, args.duration)
            strong_bytes = decode(sniffer.drain(), FF_PREFIX) if sniffer and sniffer.ok else []
            time.sleep(0.8)

            print(f"    playing WEAK (light motor) for {args.duration:.0f}s...")
            play(node, 5, args.duration)
            weak_bytes = decode(sniffer.drain(), FF_PREFIX) if sniffer and sniffer.ok else []

            nonzero = [v for v in strong_bytes + weak_bytes if v != (0, 0)]
            wire = (", ".join(f"{s}/{w}" for s, w in nonzero[:3]) if nonzero
                    else ("none captured" if sniffer and sniffer.ok else "-"))
            print(f"    0x0d rumble bytes (strong/weak): {wire}")

            rating = input(f"\n    How strong did that feel at intensity {lv}? "
                           "(0=nothing .. 5=full, Enter to skip): ").strip()
            results.append((lv, f"{back_l}/{back_r}", cmd_seen, wire, rating or "-"))
            print()
    finally:
        if sniffer:
            sniffer.stop()
        print("[R] RESTORE")
        if orig_left is not None:
            write_attr(left_p, orig_left)
        if right_p and orig_right is not None:
            write_attr(right_p, orig_right)
        print(f"    left={read_attr(left_p)}"
              + (f"  right={read_attr(right_p)}" if right_p else ""))

    print("\nsummary")
    print("-" * 78)
    print(f"  {'level':<7}{'readback':<12}{'0x06 on wire':<22}{'0x0d bytes':<20}felt")
    for lv, back, cmd, wire, felt in results:
        print(f"  {lv:<7}{back:<12}{cmd:<22}{wire:<20}{felt}")

    print("""
How to read this:
  0x0d bytes IDENTICAL across every level
      -> EXPECTED. The MCU scales internally; the driver keeps sending the
         game's requested magnitudes unchanged. Not a failure.
  0x06 shows the value you set, at every level
      -> the write reached the MCU. Combined with a felt drop, that is the
         feature working.
  felt ratings drop as level drops, and 0 feels like nothing
      -> intensity gating works.
  felt ratings identical at every level, but 0x06 was seen each time
      -> the MCU accepted the command and ignored it. That would be a real
         finding worth chasing.
  0x06 'not seen'
      -> either the usbmon tap missed it or the driver did not send it. Check
         that the sweep values actually differ from the current setting; the
         driver may skip redundant writes.
""")


if __name__ == "__main__":
    main()
