#!/usr/bin/env python3
# lamparray_probe.py - does the Ally's HID LampArray collection drive the
# joystick ring LEDs?
#
# The Ally X exposes a real HID LampArray (usage page 0x59) on MI_00, which
# ASUS itself never uses - it drives the rings over its vendor FF31 path
# instead. The kernel now has a LampArray helper in hid-generic
# (CONFIG_HID_LAMPARRAY), so if this collection actually controls the rings
# then LEDs could be handled by the standard class instead of a custom
# interface. This answers that question on real hardware.
#
# Report layouts below are taken from this device's own report descriptor,
# not from the spec:
#   id 1  LampArrayAttributes   : LampCount u16 + 5x u32
#   id 2  LampAttributesRequest : LampId u16
#   id 3  LampAttributesResponse: LampId u16 + 5x u32 + 6x u8
#   id 5  LampRangeUpdate       : flags u8 + LampIdStart/End u16 + R,G,B,I u8
#   id 6  LampArrayControl      : AutonomousMode u8
# All are Feature reports.
#
# Usage:
#   sudo python3 lamparray_probe.py            # read-only: enumerate lamps
#   sudo python3 lamparray_probe.py --write    # also try to drive the LEDs
#
# --write takes host control (AutonomousMode=0), sets every lamp red, then
# green, then restores autonomous mode. Watch the rings during it.

import argparse
import ctypes
import fcntl
import glob
import os
import struct
import sys
import time

# Both are _IOC(_IOC_WRITE|_IOC_READ, 'H', nr, len) - direction 3, not 2.
HIDIOCGFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x07
HIDIOCSFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x06

# Report ids and their total sizes (report id byte included).
RPT_ATTRS,     LEN_ATTRS     = 1, 1 + 2 + 5 * 4
RPT_LAMP_REQ,  LEN_LAMP_REQ  = 2, 1 + 2
RPT_LAMP_RESP, LEN_LAMP_RESP = 3, 1 + 2 + 5 * 4 + 6
RPT_RANGE,     LEN_RANGE     = 5, 1 + 1 + 2 + 2 + 4
RPT_CONTROL,   LEN_CONTROL   = 6, 1 + 1

LAMP_UPDATE_COMPLETE = 0x01  # LampMultiUpdateFlags bit 0

KIND = {1: "Keyboard", 2: "Mouse", 3: "GameController", 4: "Peripheral",
        5: "Scene", 6: "Notification", 7: "Chassis", 8: "Wearable",
        9: "Furniture", 10: "Art"}


def find_lamparray():
    """Return the hidraw node whose descriptor starts with the LampArray usage."""
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        rd = os.path.join(path, "device", "report_descriptor")
        try:
            head = open(rd, "rb").read(4)
        except OSError:
            continue
        # 06 59 00 = Usage Page (Lighting And Illumination), 09 01 = LampArray
        if head[:4] == bytes([0x06, 0x59, 0x00, 0x09]):
            return "/dev/" + os.path.basename(path)
    return None


def get_feature(fd, report_id, length):
    buf = bytearray(length)
    buf[0] = report_id
    fcntl.ioctl(fd, HIDIOCGFEATURE(length), buf)
    return bytes(buf)


def set_feature(fd, payload):
    buf = bytearray(payload)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="take host control and try to drive the lamps")
    ap.add_argument("--device", help="hidraw node (default: auto-detect)")
    args = ap.parse_args()

    dev = args.device or find_lamparray()
    if not dev:
        sys.exit("No HID LampArray collection found.")
    print(f"LampArray node: {dev}")

    try:
        fd = os.open(dev, os.O_RDWR)
    except PermissionError:
        sys.exit(f"Permission denied on {dev} - run with sudo.")

    with os.fdopen(fd, "rb+", buffering=0) as f:
        fd = f.fileno()

        # --- attributes -------------------------------------------------
        try:
            r = get_feature(fd, RPT_ATTRS, LEN_ATTRS)
        except OSError as e:
            sys.exit(f"LampArrayAttributes GET failed: {e.strerror}")
        count, w, h, d, kind, interval = struct.unpack("<HIIIII", r[1:])
        print(f"  lamp count      : {count}")
        print(f"  bounding box    : {w} x {h} x {d} um")
        print(f"  kind            : {kind} ({KIND.get(kind, 'unknown')})")
        print(f"  min update intvl: {interval} us")

        if count == 0:
            print("\n  No lamps reported - this collection controls nothing.")
            return

        # --- per-lamp attributes ---------------------------------------
        print(f"\n  per-lamp attributes (first {min(count, 8)}):")
        for lamp in range(min(count, 8)):
            try:
                set_feature(fd, struct.pack("<BH", RPT_LAMP_REQ, lamp))
                r = get_feature(fd, RPT_LAMP_RESP, LEN_LAMP_RESP)
            except OSError as e:
                print(f"    lamp {lamp}: request/response failed ({e.strerror})")
                continue
            lid, px, py, pz, latency, purposes = struct.unpack("<HIIIII", r[1:23])
            red, green, blue, intensity, programmable, binding = r[23:29]
            print(f"    lamp {lid}: pos=({px},{py},{pz})um purposes=0x{purposes:x} "
                  f"levels R{red}/G{green}/B{blue}/I{intensity} "
                  f"programmable={programmable}")

        if not args.write:
            print("\nRead-only. Re-run with --write to try driving the lamps.")
            return

        # --- drive the lamps -------------------------------------------
        print("\n  taking host control (AutonomousMode=0)...")
        try:
            set_feature(fd, struct.pack("<BB", RPT_CONTROL, 0))
        except OSError as e:
            print(f"    control report failed: {e.strerror}")

        for name, (red, green, blue) in (("RED", (255, 0, 0)),
                                         ("GREEN", (0, 255, 0)),
                                         ("BLUE", (0, 0, 255))):
            print(f"    all lamps -> {name}   (watch the rings)")
            try:
                set_feature(fd, struct.pack("<BBHHBBBB", RPT_RANGE,
                                            LAMP_UPDATE_COMPLETE,
                                            0, count - 1,
                                            red, green, blue, 255))
            except OSError as e:
                print(f"      range update failed: {e.strerror}")
                break
            time.sleep(2)

        print("  restoring autonomous mode...")
        try:
            set_feature(fd, struct.pack("<BB", RPT_CONTROL, 1))
        except OSError as e:
            print(f"    control report failed: {e.strerror}")

        print("\nDid the joystick rings change colour? That is the answer.")


if __name__ == "__main__":
    main()
