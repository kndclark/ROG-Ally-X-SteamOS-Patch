#!/usr/bin/env python3
# remap_deep_probe.py - the three remaining local questions about why
# CMD_SET_MAPPING (0x02) is a no-op on the Ally X.
#
# Settled so far, all on hardware:
#   - the packet is byte-identical to the upstream hid-asus-ally reference
#     (framing, pair index 0x08, 44-byte block, value offsets 1/2/4/3)
#   - the missing check-ready handshake is NOT the cause
#   - the interface is NOT the cause (1.1-1.5 all answer identically, and
#     writing the mapping to every one of them changes nothing)
#   - the firmware is NOT missing the feature: investigations/paddle-mapping.md
#     shows Armoury Crate successfully remapping M1/M2 on this same Ally X,
#     in DESKTOP mode
#
# Open: subopcode 0x0A answers 0x00 while eight other capability queries answer
# 0x01. Our driver calls 0x0A "CMD_CHECK_READY" and discards that byte; the
# Armoury Crate enum calls it CheckCustomButtonStatus. It is the only query
# named "Status" rather than "Support", which is why "current state" is now a
# better reading than "unsupported".
#
# Three stages:
#   [1] FULL DUMP  - all 64 bytes of the 0x0A response. We have only ever
#                    printed 16, so a per-button bitmask could be sitting in
#                    bytes we have not looked at.
#   [2] STATE      - query 0x0A, write a mapping, query 0x0A again. If byte 4
#                    flips 0x00 -> 0x01, it tracks state and the write IS
#                    landing somewhere, which changes the diagnosis entirely.
#   [3] MODE       - the MCU keeps a SEPARATE mapping table per gamepad mode,
#                    and the Windows remap that worked was done in desktop
#                    mode while we have only ever tested in gamepad mode.
#                    Repeat the write-and-watch in both modes.
#
# Stage 1 is read-only. Stages 2 and 3 write mappings and (stage 3) switch
# gamepad mode; both are restored on exit, including on Ctrl-C.
#
# NOTE: desktop mode turns the sticks and buttons into keyboard/mouse input.
# Make sure no text field is focused before running.
#
# Usage: sudo python3 remap_deep_probe.py

import fcntl
import glob
import os
import select
import sys
import time

HIDIOCSFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x06
HIDIOCGFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x07

REPORT_SIZE = 64
REPORT_ID = 0x5A
CODE_PAGE = 0xD1
CMD_SET_MAPPING = 0x02
CMD_CUSTOM_BTN_STATUS = 0x0A
LEN_MAPPING = 0x2C
BTN_CODE_LEN = 11
PAIR_M1M2 = 0x08

T_PAD, T_KB, T_MOUSE, T_MEDIA = 0x01, 0x02, 0x03, 0x05
OFFSET = {T_PAD: 1, T_KB: 2, T_MOUSE: 4, T_MEDIA: 3}

CODE_MEDIA_VOL_UP = (T_MEDIA, 0x03)
CODE_KB_M1 = (T_KB, 0x8F)   # stock M1
CODE_KB_M2 = (T_KB, 0x8E)   # stock M2
CODE_NONE = (0x00, 0x00)


def entry(code):
    out = bytearray(BTN_CODE_LEN)
    btype, value = code
    if btype:
        out[0] = btype
        out[OFFSET[btype]] = value
    return bytes(out)


def mapping_packet(pair, first, second):
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    buf[1] = CODE_PAGE
    buf[2] = CMD_SET_MAPPING
    buf[3] = pair
    buf[4] = LEN_MAPPING
    buf[5:16] = entry(first)
    buf[16:27] = entry(CODE_NONE)
    buf[27:38] = entry(second)
    buf[38:49] = entry(CODE_NONE)
    return bytes(buf)


def status_request():
    """0x0A with byte 4 = 0x00, matching the driver's ally_check_capability."""
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    buf[1] = CODE_PAGE
    buf[2] = CMD_CUSTOM_BTN_STATUS
    buf[3] = 0x01
    buf[4] = 0x00
    return bytes(buf)


def ally_interfaces():
    found = {}
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
                if vid == "0b05" and pid == "1b4c":
                    found.setdefault(num, "/dev/" + os.path.basename(hr))
                break
            path = os.path.dirname(path)
    return found


def mode_attr():
    hits = glob.glob("/sys/bus/hid/devices/*1B4C*/gamepad_mode")
    return hits[0] if hits else None


def read_mode(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def write_mode(path, value):
    try:
        with open(path, "w") as f:
            f.write(value)
        time.sleep(0.4)
        return True
    except OSError as e:
        print(f"    could not set mode to {value}: {e.strerror}")
        return False


def set_feature(fd, payload):
    fcntl.ioctl(fd, HIDIOCSFEATURE(REPORT_SIZE), bytearray(payload))


def get_feature(fd):
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    fcntl.ioctl(fd, HIDIOCGFEATURE(REPORT_SIZE), buf)
    return bytes(buf)


def query_status(fd):
    """Returns the full 64-byte response to the 0x0A query."""
    set_feature(fd, status_request())
    time.sleep(0.02)
    return get_feature(fd)


def dump64(data, indent="    "):
    for off in range(0, len(data), 16):
        row = " ".join(f"{b:02x}" for b in data[off:off + 16])
        print(f"{indent}{off:02x}: {row}")


def watch(nodes, seconds, label):
    fds, names = [], []
    for num, node in sorted(nodes.items()):
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
            names.append(f"1.{num}")
        except OSError:
            pass
    print(f"  >>> press M1 a few times now ({seconds}s)...")
    seen = []
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            ready, _, _ = select.select(fds, [], [], max(0, deadline - time.time()))
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except (BlockingIOError, OSError):
                    continue
                if not data:
                    continue
                line = f"[{names[fds.index(fd)]}] " + " ".join(f"{b:02x}" for b in data[:8])
                if line not in seen:
                    print(f"    {line}")
                seen.append(line)
    finally:
        for fd in fds:
            os.close(fd)
    if not seen:
        print("    (no reports seen)")
    print(f"  == {label} ==\n")
    return sorted(set(seen))


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    if 2 not in nodes:
        sys.exit("No ROG Ally X config interface (1.2) found.")
    cfg_node = nodes[2]
    mattr = mode_attr()
    orig_mode = read_mode(mattr) if mattr else None

    print(f"config interface : {cfg_node}")
    print(f"gamepad_mode     : {mattr or 'not exposed'} = {orig_mode}\n")

    fd = os.open(cfg_node, os.O_RDWR)
    try:
        # ---- stage 1: the whole 0x0A response --------------------------
        print("[1] FULL 0x0A RESPONSE - all 64 bytes, not just the first 16")
        base_status = query_status(fd)
        dump64(base_status)
        print(f"    byte 4 = 0x{base_status[4]:02x}")
        nonzero = [i for i, b in enumerate(base_status) if b and i > 4]
        print(f"    non-zero bytes past byte 4: {nonzero or 'none'}\n")

        # ---- stage 2: does 0x0A track state? ---------------------------
        print("[2] STATE TRACKING - query, write a mapping, query again")
        before = query_status(fd)
        print(f"    before write : byte 4 = 0x{before[4]:02x}")
        set_feature(fd, mapping_packet(PAIR_M1M2, CODE_MEDIA_VOL_UP, CODE_KB_M2))
        time.sleep(0.3)
        after = query_status(fd)
        print(f"    after write  : byte 4 = 0x{after[4]:02x}")
        if before == after:
            print("    -> response IDENTICAL; 0x0A does not track the write")
        else:
            print("    -> response CHANGED; 0x0A reflects mapping state")
            diff = [i for i in range(REPORT_SIZE) if before[i] != after[i]]
            print(f"       bytes that differ: {diff}")
            dump64(after)
        set_feature(fd, mapping_packet(PAIR_M1M2, CODE_KB_M1, CODE_KB_M2))
        time.sleep(0.2)
        print()

        # ---- stage 3: per-mode mapping tables --------------------------
        print("[3] GAMEPAD MODE - the MCU keeps one mapping table per mode,")
        print("    and the Windows remap that worked was done in desktop mode.\n")
        if not mattr or not orig_mode:
            print("    gamepad_mode not available - skipping stage 3\n")
        else:
            for mode in ("gamepad", "desktop"):
                print(f"  --- mode: {mode} ---")
                if not write_mode(mattr, mode):
                    continue
                print(f"    mode now reads: {read_mode(mattr)}")
                st = query_status(fd)
                print(f"    0x0A byte 4 in {mode} mode: 0x{st[4]:02x}")
                base = watch(nodes, 5, f"{mode}: stock binding")

                set_feature(fd, mapping_packet(PAIR_M1M2, CODE_MEDIA_VOL_UP,
                                               CODE_KB_M2))
                time.sleep(0.3)
                st2 = query_status(fd)
                print(f"    0x0A byte 4 after write: 0x{st2[4]:02x}")
                after_w = watch(nodes, 5, f"{mode}: after remap to MEDIA_VOL_UP")

                if base != after_w:
                    print("    *** PADDLE BEHAVIOR CHANGED IN THIS MODE ***\n")
                else:
                    print("    (no change)\n")

                set_feature(fd, mapping_packet(PAIR_M1M2, CODE_KB_M1, CODE_KB_M2))
                time.sleep(0.2)
    finally:
        # ---- restore ---------------------------------------------------
        print("[R] RESTORE")
        try:
            set_feature(fd, mapping_packet(PAIR_M1M2, CODE_KB_M1, CODE_KB_M2))
            print("    stock M1/M2 binding rewritten")
        except OSError as e:
            print(f"    mapping restore failed: {e.strerror}")
        os.close(fd)
        if mattr and orig_mode:
            if write_mode(mattr, orig_mode):
                print(f"    gamepad_mode restored to {read_mode(mattr)}")

    print("""
How to read this:
  [2] byte 4 flips after the write
      -> 0x0A is a state flag, the mapping IS being stored, and the paddle is
         being overridden by something else (most likely the persisted
         "use as secondary function" routing).
  [3] the paddle changes in desktop mode but not gamepad mode
      -> remapping works, it is just per-mode, and the driver is writing to
         the table for whichever mode is active. That would make this a
         driver-side mode bug rather than a firmware limitation.
  [3] 0x0A reads 0x01 in desktop mode
      -> the flag is mode-scoped, and gamepad mode genuinely does not support
         custom paddle bindings on the Ally X.
  everything identical everywhere
      -> local avenues are exhausted; the Windows capture of Armoury Crate
         performing the remap is the next move.
""")


if __name__ == "__main__":
    main()
