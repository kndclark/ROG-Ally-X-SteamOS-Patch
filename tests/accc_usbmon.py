#!/usr/bin/env python3
# accc_usbmon.py - the AC/CC timeline, captured BELOW the HID driver.
#
# READ-ONLY on the device. Stops InputPlumber for the duration and restarts it.
#
# Why not hidraw
# --------------
# accc_timeline.py watched hidraw on interface 1.2 and never once saw the press
# bytes (0x38 / 0xA6 / 0xA7) - only the 0x00 release. That is structural, not
# bad luck: handle_ally_event() consumes those reports, asus_raw_event returns
# -1, and hid_input_report() does `goto unlock` before hid_report_raw_event(),
# which is what feeds hidraw. The bytes we most need are exactly the ones our
# own driver eats. investigations/hid-observability.md documents this trap; the
# first tool walked into it anyway.
#
# usbmon taps the USB layer beneath HID, so a report consumed by the driver is
# still visible. This merges usbmon with evdev into one timeline, which is what
# makes the press/release question answerable.
#
# The question it settles
# -----------------------
# Does the MCU send a distinct press edge and release edge, or does it fire
# both at release (as Nero describes for the original Ally)?
#
#   separate edges, seconds apart while held
#       -> a real held key is achievable; hid-asus-ally's pattern
#          `input_report_key(input, KEY_F16, data[1] == 0xA6)` is correct and
#          our instantaneous tap is simply wrong.
#   both edges ~1 ms apart at release
#       -> holding is impossible from the wire, and any fix has to synthesise
#          the hold with a timer. Much bigger design question.
#
# Usage: sudo python3 accc_usbmon.py

import glob
import os
import re
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

VENDOR = {
    0x38: "AC press", 0x93: "AC (Ally X variant)", 0xA5: "paddle",
    0xA6: "CC / QAM", 0xA7: "long press", 0xA8: "long press RELEASE",
    0x00: "release",
}

# usbmon endpoint number -> the interface it belongs to on this device.
EP_IFACE = {1: "1.3 keyboard", 3: "1.2 config", 5: "1.4", 7: "1.5 gamepad"}

STEPS = [
    ("AC tap", "tap AC once, quickly", 6),
    ("AC hold 4s", "press AC and HOLD ~4s, then release", 9),
    ("CC tap", "tap CC once, quickly", 6),
    ("CC hold 4s", "press CC and HOLD ~4s, then release", 9),
    ("CC held + A", "hold CC down the WHOLE time, press A twice, then release CC", 9),
]

# Real line, captured on this device:
#   ffff895851b43c80 2692951150 C Ii:1:009:3 0:1 6 = 5a380000 0000
# The status field is "0:1" (status:interval) on interrupt endpoints, not a
# bare integer. An earlier version of this regex assumed "0" and matched
# nothing at all, which is why the first usbmon run captured zero lines.
USBMON_RE = re.compile(
    r"^\S+\s+(\d+)\s+([SCE])\s+([A-Za-z]+):(\d+):(\d+):(\d+)\s+"
    r"(-?\d+(?::\d+)?)\s+(\d+)(?:\s+=\s+(.*))?$"
)


def ally_bus_dev():
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            vid = open(os.path.join(d, "idVendor")).read().strip()
            pid = open(os.path.join(d, "idProduct")).read().strip()
            if vid == "0b05" and pid in ("1b4c", "1abe"):
                return (int(open(os.path.join(d, "busnum")).read()),
                        int(open(os.path.join(d, "devnum")).read()), pid)
        except OSError:
            continue
    return None, None, None


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


def decode_usb(line, want_dev):
    """Return (iface, hexstr, note) for an inbound completion, else None."""
    m = USBMON_RE.match(line.strip())
    if not m:
        return None
    _, etype, xfer, bus, dev, ep, status, length, data = m.groups()
    if etype != "C" or not xfer.startswith("I") or int(dev) != want_dev:
        return None
    if not data:
        return None
    raw = data.replace(" ", "")
    try:
        b = bytes.fromhex(raw[:16])
    except ValueError:
        return None
    if not b:
        return None
    iface = EP_IFACE.get(int(ep), f"ep{ep}")
    note = ""
    if b[0] == 0x5A and len(b) > 1:
        note = VENDOR.get(b[1], f"unknown 0x{b[1]:02x}")
    elif b[0] == 0x01 and len(b) > 3:
        note = f"keyboard mod=0x{b[1]:02x} key=0x{b[3]:02x}"
    return iface, " ".join(f"{x:02x}" for x in b[:8]), note


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    bus, dev, pid = ally_bus_dev()
    if bus is None:
        sys.exit("ROG Ally not found on USB.")
    print(f"Ally: bus {bus} device {dev} (0b05:{pid})")

    subprocess.run(["modprobe", "usbmon"], check=False)
    node = f"/sys/kernel/debug/usb/usbmon/{bus}u"
    if not os.path.exists(node):
        node = "/sys/kernel/debug/usb/usbmon/0u"
    print(f"usbmon node: {node}")

    nodes = ally_nodes()
    ip = subprocess.run(["systemctl", "is-active", "--quiet",
                         "inputplumber"]).returncode == 0
    if ip:
        print("\nstopping inputplumber...")
        subprocess.run(["systemctl", "stop", "inputplumber"])
        time.sleep(2.0)

    fds, labels = [], []
    mon = None
    try:
        # Read usbmon through `stdbuf -oL cat`, the approach ff_sniff.sh uses.
        # Opening the text node directly with O_NONBLOCK and driving it through
        # select() captured nothing at all - a pipe behaves predictably.
        mon = subprocess.Popen(["stdbuf", "-oL", "cat", node],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(mon.stdout.fileno(), False)
        fds.append(mon.stdout.fileno())
        labels.append("usbmon")
        print(f"  {node:<40} OK (via pipe)")

        for p in sorted(nodes):
            try:
                fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
                labels.append(os.path.basename(p))
                print(f"  {p:<40} OK")
            except OSError as e:
                print(f"  {p:<40} FAILED: {e.strerror}")

        # Rule 1 of investigations/hid-observability.md: prove the channel
        # carries the signal BEFORE drawing conclusions from its silence. The
        # previous version of this script skipped this and produced five steps
        # of evdev-only output that looked like data.
        input("\n[self-test] Enter, then wiggle a stick and press any buttons... ")
        seen = 0
        pending = b""
        end = time.monotonic() + 4
        while time.monotonic() < end:
            ready, _, _ = select.select(fds, [], [], max(0, end - time.monotonic()))
            for fd in ready:
                try:
                    data = os.read(fd, 65536)
                except (BlockingIOError, OSError):
                    continue
                if data and labels[fds.index(fd)] == "usbmon":
                    pending += data
                    *lines, pending = pending.split(b"\n")
                    for ln in lines:
                        if decode_usb(ln.decode("utf-8", "replace"), dev):
                            seen += 1
        print(f"    usbmon lines from this device: {seen}")
        if seen == 0:
            print("""
    usbmon captured NOTHING. The channel is not working, so every step below
    would produce evdev-only output that looks like a finding but is not.
    Aborting rather than generating misleading data.""")
            return
        print("    channel verified, proceeding.\n")

        print("Press Enter before each step, then perform ONLY that action.")
        for name, prompt, secs in STEPS:
            input(f"\n[{name}] Enter when ready... ")
            for fd in fds:
                try:
                    os.read(fd, 1 << 20)
                except (BlockingIOError, OSError):
                    pass
            print(f"--- {prompt} ---\n    recording {secs}s, go...")

            pending = b""
            events = []
            t0 = time.monotonic()
            end = t0 + secs
            while time.monotonic() < end:
                ready, _, _ = select.select(fds, [], [],
                                            max(0, end - time.monotonic()))
                now = (time.monotonic() - t0) * 1000.0
                for fd in ready:
                    label = labels[fds.index(fd)]
                    try:
                        data = os.read(fd, 65536)
                    except (BlockingIOError, OSError):
                        continue
                    if not data:
                        continue
                    if label == "usbmon":
                        pending += data
                        *lines, pending = pending.split(b"\n")
                        for ln in lines:
                            got = decode_usb(ln.decode("utf-8", "replace"), dev)
                            if got:
                                iface, hexs, note = got
                                events.append((now, f"USB {iface}",
                                               f"{hexs}   {note}"))
                    else:
                        for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                            _, _, et, code, val = struct.unpack(
                                EV_FORMAT, data[off:off + EV_SIZE])
                            if et == EV_KEY:
                                events.append(
                                    (now, f"evdev {label}",
                                     f"{KEYS.get(code, f'code {code}')} "
                                     f"{VALUES.get(val, val)}"))
            if not events:
                print("      (nothing captured)")
            for t, src, what in events:
                print(f"      {t:8.1f} ms  {src:<18} {what}")
    finally:
        # The usbmon fd belongs to the subprocess pipe; closing it here as well
        # as via terminate() raises EBADF at interpreter shutdown.
        mon_fd = mon.stdout.fileno() if mon else -1
        for fd in fds:
            if fd == mon_fd:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
        if mon:
            mon.terminate()
            mon.wait(timeout=2)
        if ip:
            subprocess.run(["systemctl", "start", "inputplumber"])
            print("\ninputplumber restarted")

    print("""
The decisive lines are the USB ones, which hidraw could not show:

  a '5a a6' (or 5a 38 / 5a a7) at PRESS and a '5a 00' seconds later at RELEASE
      -> distinct edges. A held key works, and the fix is hid-asus-ally's
         `input_report_key(input, KEY, data[1] == 0xA6)` pattern.
  '5a a6' and '5a 00' arriving ~1 ms apart, only when you let go
      -> both edges fire at release. Holding cannot be represented, and the
         driver would have to synthesise it.
  'CC held + A': whether any CC release byte appears before the A presses.
""")


if __name__ == "__main__":
    main()
