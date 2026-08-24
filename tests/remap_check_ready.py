#!/usr/bin/env python3
# remap_check_ready.py - does CMD_SET_MAPPING need a check-ready handshake first?
#
# On the Ally X, writing any code to btn_m1/remap has no effect: the paddle keeps
# emitting the vendor code 0xA5. Diffing our driver against the upstream
# hid-asus-ally reference (which works on the original Ally) turns up exactly one
# difference in this path: the reference calls __gamepad_check_ready() before
# EVERY setter, including __gamepad_set_mapping(). Our driver only calls
# ally_gamepad_check_ready() at probe and resume, never on the remap path -
# despite its own doc comment saying "This should be called before any remapping
# attempts".
#
# Everything else in the packet was verified byte-for-byte identical to the
# reference: header 5A D1 02, byte[3]=pair, byte[4]=0x2C, the 44-byte block of
# four 11-byte entries at [5], pair m1_m2 = 0x08, and the per-type value offsets
# (pad=1, kb=2, media=3, mouse=4).
#
# So this runs the two arms as a controlled experiment:
#   A. mapping packet alone            -> expected to do nothing (the bug)
#   B. check-ready, then the SAME packet -> expected to take effect
#
# Observation is by reading the raw HID input reports off both the config
# interface (1.2, ep 0x83 - where the paddle's vendor 0xA5 appears) and the
# keyboard interface (1.3, ep 0x81 - where a remapped key would appear). That is
# ground truth straight from the MCU, and it is unaffected by InputPlumber's
# evdev grabs, so nothing needs to be stopped to run this.
#
# Writes: 2 mapping packets during the test + 1 to restore the default. That is
# the same order of magnitude Armoury Crate does on a settings change.
#
# Usage: sudo python3 remap_check_ready.py

import fcntl
import glob
import os
import select
import struct
import sys
import time

HIDIOCSFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x06
HIDIOCGFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x07

REPORT_SIZE = 64
REPORT_ID = 0x5A
CODE_PAGE = 0xD1
CMD_SET_MAPPING = 0x02
CMD_CHECK_READY = 0x0A
LEN_MAPPING = 0x2C          # 44 = 4 entries x 11 bytes
BTN_CODE_LEN = 11
PAIR_M1M2 = 0x08

# (type, value-byte offset within the 11-byte entry)
T_PAD, T_KB, T_MOUSE, T_MEDIA = 0x01, 0x02, 0x03, 0x05
OFFSET = {T_PAD: 1, T_KB: 2, T_MOUSE: 4, T_MEDIA: 3}

CODE_MEDIA_VOL_UP = (T_MEDIA, 0x03)
CODE_KB_M1 = (T_KB, 0x8F)   # the stock M1 binding
CODE_KB_M2 = (T_KB, 0x8E)   # the stock M2 binding
CODE_NONE = (0x00, 0x00)


def entry(code):
    """Build one 11-byte mapping entry."""
    out = bytearray(BTN_CODE_LEN)
    btype, value = code
    if btype:
        out[0] = btype
        out[OFFSET[btype]] = value
    return bytes(out)


def mapping_packet(pair, first, second):
    """Build the full 64-byte CMD_SET_MAPPING feature report."""
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    buf[1] = CODE_PAGE
    buf[2] = CMD_SET_MAPPING
    buf[3] = pair
    buf[4] = LEN_MAPPING
    buf[5:16] = entry(first)         # first (M1) remap
    buf[16:27] = entry(CODE_NONE)    # first macro
    buf[27:38] = entry(second)       # second (M2) remap
    buf[38:49] = entry(CODE_NONE)    # second macro
    return bytes(buf)


def find_iface(ifnum):
    """hidraw node for ROG Ally X USB interface `ifnum`."""
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
                if vid == "0b05" and pid == "1b4c" and num == ifnum:
                    return "/dev/" + os.path.basename(hr)
                break
            path = os.path.dirname(path)
    return None


def set_feature(fd, payload):
    buf = bytearray(payload)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)


def get_feature(fd, report_id, length=REPORT_SIZE):
    buf = bytearray(length)
    buf[0] = report_id
    fcntl.ioctl(fd, HIDIOCGFEATURE(length), buf)
    return bytes(buf)


def check_ready(fd, tries=3):
    """Mirror of the reference __gamepad_check_ready(): poll until the MCU
    echoes back the check-ready subopcode."""
    for i in range(tries):
        req = bytearray(REPORT_SIZE)
        req[0] = REPORT_ID
        req[1] = CODE_PAGE
        req[2] = CMD_CHECK_READY
        req[3] = 0x01
        try:
            set_feature(fd, req)
            resp = get_feature(fd, REPORT_ID)
        except OSError as e:
            print(f"    ready poll {i + 1}/{tries}: ioctl failed ({e.strerror})")
            continue
        print(f"    ready poll {i + 1}/{tries}: resp[0:4] = {resp[0]:02x} "
              f"{resp[1]:02x} {resp[2]:02x} {resp[3]:02x}")
        if resp[2] == CMD_CHECK_READY:
            return True
        time.sleep(0.002)
    return False


def watch(fds, names, seconds, label):
    """Print every HID input report seen on the watched interfaces."""
    print(f"  >>> press M1 a few times now ({seconds}s)...")
    seen = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        ready, _, _ = select.select(fds, [], [], max(0, deadline - time.time()))
        for fd in ready:
            try:
                data = os.read(fd, 64)
            except (BlockingIOError, OSError):
                continue
            if not data:
                continue
            src = names[fds.index(fd)]
            hexs = " ".join(f"{b:02x}" for b in data[:8])
            seen.append((src, hexs))
            print(f"    [{src}] {hexs}")
    if not seen:
        print("    (no reports seen - was the paddle pressed?)")
    print(f"  == {label}: {len(seen)} report(s) ==\n")
    return seen


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    cfg_node = find_iface(2)     # 1.2 config interface, ep 0x83
    kbd_node = find_iface(3)     # 1.3 keyboard interface, ep 0x81
    if not cfg_node:
        sys.exit("Could not find the Ally config interface (1.2).")
    print(f"config   iface 1.2 : {cfg_node}")
    print(f"keyboard iface 1.3 : {kbd_node or 'not found (continuing)'}\n")

    cfg_fd = os.open(cfg_node, os.O_RDWR | os.O_NONBLOCK)
    watch_fds, watch_names = [cfg_fd], ["1.2"]
    kbd_fd = None
    if kbd_node:
        kbd_fd = os.open(kbd_node, os.O_RDONLY | os.O_NONBLOCK)
        watch_fds.append(kbd_fd)
        watch_names.append("1.3")

    pkt = mapping_packet(PAIR_M1M2, CODE_MEDIA_VOL_UP, CODE_KB_M2)
    print("mapping packet (first 16 bytes):")
    print("  " + " ".join(f"{b:02x}" for b in pkt[:16]) + " ...\n")

    try:
        print("[0] BASELINE - no packet sent yet")
        watch(watch_fds, watch_names, 6, "baseline")

        print("[A] mapping packet WITHOUT check-ready")
        set_feature(cfg_fd, pkt)
        time.sleep(0.3)
        arm_a = watch(watch_fds, watch_names, 6, "arm A")

        print("[B] check-ready, THEN the same mapping packet")
        ok = check_ready(cfg_fd)
        print(f"    MCU ready: {ok}")
        set_feature(cfg_fd, pkt)
        time.sleep(0.3)
        arm_b = watch(watch_fds, watch_names, 6, "arm B")

        print("[R] RESTORE - default M1/M2 binding")
        check_ready(cfg_fd)
        set_feature(cfg_fd, mapping_packet(PAIR_M1M2, CODE_KB_M1, CODE_KB_M2))
        time.sleep(0.3)
        watch(watch_fds, watch_names, 6, "restored")
    finally:
        os.close(cfg_fd)
        if kbd_fd is not None:
            os.close(kbd_fd)

    print("""
How to read this:
  A still shows 5A A5 (same as baseline) and B shows something different
      -> CONFIRMED: the missing check-ready handshake is the bug. The fix is to
         call ally_gamepad_check_ready() at the top of ally_set_button_mapping().
  A and B both still show 5A A5
      -> the handshake is not the cause. Next suspect is the Armoury Crate
         "use as secondary function" binding persisted in the MCU, which
         investigations/paddle-mapping.md showed makes both paddles emit the
         shared 5A A5 code regardless of their assigned keys.
  A already changes the reports
      -> the remap works when driven directly, so the driver's own call path
         (not the packet) is at fault.
""")


if __name__ == "__main__":
    main()
