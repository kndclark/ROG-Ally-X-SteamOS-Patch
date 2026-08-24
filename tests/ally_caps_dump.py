#!/usr/bin/env python3
# ally_caps_dump.py - read every capability byte the Ally MCU will report.
#
# READ-ONLY. Sends only Check* queries, writes no configuration, touches no
# LED path. Safe to run any time, and safe to hand to another Ally owner.
#
# Why this exists
# ---------------
# On the Ally X, CMD_SET_MAPPING (0x02) is accepted and echoed by the MCU but
# changes nothing, on every interface, with or without a check-ready handshake.
# The interface sweep turned up the reason: subopcode 0x0A answers 0x00 while
# every other capability query answers 0x01.
#
# 0x0A is what hid-asus calls CMD_CHECK_READY and uses purely as a ping - it
# tests only that the subopcode echoed back, and throws the answer byte away:
#
#     if (buf[1] == HID_ALLY_FEATURE_CODE_PAGE && buf[2] == check_cmd)
#             result = (buf[4] == 0x01);          <- ally_check_capability
#     ...
#     if (buf[2] == CMD_CHECK_READY)              <- ally_gamepad_check_ready
#             return 0;                              (never looks at buf[4])
#
# But the Armoury Crate enum in gamepad-protocol.md names 0x0A
# CheckCustomButtonStatus - a capability query, not a ready ping. If byte 4 is
# genuinely 0 on this device, custom button mapping is reported unsupported or
# disabled, which would explain the silent no-op exactly.
#
# The previous sweep sent payload 0x01, so a 0x01 answer was ambiguous with a
# plain echo. This sends payload 0x00 (matching the driver's own
# ally_check_capability) so any 0x01 that comes back must have been written by
# the device.
#
# Usage: sudo python3 ally_caps_dump.py

import fcntl
import glob
import os
import sys
import time

HIDIOCSFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x06
HIDIOCGFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x07

REPORT_SIZE = 64
REPORT_ID = 0x5A
CODE_PAGE = 0xD1

# Every Check* subopcode known from hid-asus's enum ally_command_codes plus the
# Armoury Crate names in gamepad-protocol.md. All read-only.
QUERIES = [
    (0x0A, "CheckCustomButtonStatus", "driver calls this CMD_CHECK_READY"),
    (0x0C, "CheckXboxControllerStatusSupport", ""),
    (0x0E, "CheckUserCalSupport", ""),
    (0x10, "CheckTurboSupport", ""),
    (0x11, "CheckNewDefaultDeadZoneSupport", "not queried by the driver"),
    (0x12, "CheckJoystickRespCurveSupport", ""),
    (0x14, "CheckJoystickDirToXboxBtnSupport", ""),
    (0x16, "CheckGyroToJoystickSupport", ""),
    (0x17, "CheckAntiDeadzoneSupport", ""),
    (0x1A, "CheckButtonToTriggerSupport", "not queried by the driver"),
]


def find_cfg_iface():
    """hidraw node for the Ally config interface (1.2)."""
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
                if vid == "0b05" and pid in ("1abe", "1b4c") and num == 2:
                    return "/dev/" + os.path.basename(hr), pid
                break
            path = os.path.dirname(path)
    return None, None


def query(fd, cmd):
    """Send a Check* query with payload 0x00 and return the response."""
    req = bytearray(REPORT_SIZE)
    req[0] = REPORT_ID
    req[1] = CODE_PAGE
    req[2] = cmd
    req[3] = 0x01
    req[4] = 0x00          # the driver sends 0 here; any 1 back is the device
    fcntl.ioctl(fd, HIDIOCSFEATURE(REPORT_SIZE), req)
    time.sleep(0.02)
    resp = bytearray(REPORT_SIZE)
    resp[0] = REPORT_ID
    fcntl.ioctl(fd, HIDIOCGFEATURE(REPORT_SIZE), resp)
    return bytes(resp)


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    node, pid = find_cfg_iface()
    if not node:
        sys.exit("No ROG Ally config interface (1.2) found.")
    model = {"1abe": "ROG Ally (original)", "1b4c": "ROG Ally X"}.get(pid, pid)
    print(f"device : {model}  (0b05:{pid})")
    print(f"node   : {node}\n")
    print(f"{'sub':<6} {'name':<36} {'byte4':<6} answer")
    print("-" * 74)

    fd = os.open(node, os.O_RDWR)
    try:
        for cmd, name, note in QUERIES:
            try:
                resp = query(fd, cmd)
            except OSError as e:
                print(f"0x{cmd:02x}   {name:<36} {'-':<6} ioctl failed ({e.strerror})")
                continue
            if resp[1] != CODE_PAGE or resp[2] != cmd:
                print(f"0x{cmd:02x}   {name:<36} {'-':<6} no answer "
                      f"(got {resp[1]:02x} {resp[2]:02x})")
                continue
            answer = "SUPPORTED" if resp[4] == 0x01 else "NOT SUPPORTED"
            suffix = f"   <- {note}" if note else ""
            print(f"0x{cmd:02x}   {name:<36} 0x{resp[4]:02x}   {answer}{suffix}")
    finally:
        os.close(fd)

    print("""
We send byte 4 as 0x00, so any 0x01 coming back was written by the device -
these are real answers, not an echo of the request.

The question this settles: does 0x0A report NOT SUPPORTED while the others
report SUPPORTED? If so, the Ally X firmware is declaring custom button
mapping unavailable, which is why CMD_SET_MAPPING is accepted and ignored.

If you are running this on an ORIGINAL Ally where M1/M2 remap works, the
expectation is that 0x0A reports SUPPORTED there. That single line is the
whole comparison.
""")


if __name__ == "__main__":
    main()
