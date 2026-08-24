#!/usr/bin/env python3
# paddle_slot_order.py - are the M1 and M2 mapping slots swapped in our driver?
#
# G-Helper (seerge/g-helper, app/Ally/AllyControl.cs) is a working Armoury Crate
# replacement that binds Ally paddles from userspace. Its M1M2 zone is the ONLY
# zone in its whole binding table where left and right are inverted:
#
#     case BindingZone.AB:    KeyL1 = "bind_a";  KeyR1 = "bind_b";    // natural
#     case BindingZone.M1M2:  KeyL1 = "bind_m2"; KeyR1 = "bind_m1";   // INVERTED
#
#     DecodeBinding(KeyL1).CopyTo(bindings, 5);    // offset 5  = M2
#     DecodeBinding(KeyR1).CopyTo(bindings, 27);   // offset 27 = M1
#
# Our driver maps ALLY_BTN_M1 to is_first=true, i.e. offset 5 - the slot
# G-Helper says is M2. If that is right, writing btn_m1 programs M2 and vice
# versa.
#
# This matters because it would have made the previous experiment lie:
# paddle_vs_button.py remapped btn_m1 and then watched while M1 was pressed.
# Under a swap the write lands on M2, pressing M1 shows nothing, and the result
# reads as "the paddle ignored the remap" when both writes actually worked.
#
# So: remap ONE paddle, then press BOTH, and see which one moved.
#
#   btn_m1 -> KB_F8, press M1  : if F8 appears, our driver is correct
#   btn_m1 -> KB_F8, press M2  : if F8 appears, the slots ARE swapped
#
# Stage 2 separately tests whether populating the secondary slot matters.
# G-Helper defaults it to the paddle vendor codes (02-8F / 02-8E) while our
# driver writes zeros there, and an all-zero secondary slot is a candidate
# cause of the fused "both paddles emit 5a a5" behavior.
#
# Bindings are restored on exit, including on Ctrl-C.
#
# Usage: sudo python3 paddle_slot_order.py

import fcntl
import glob
import os
import select
import sys
import time

HIDIOCSFEATURE = lambda n: (3 << 30) | (n << 16) | (ord('H') << 8) | 0x06

REPORT_SIZE = 64
REPORT_ID = 0x5A
CODE_PAGE = 0xD1
CMD_SET_MAPPING = 0x02
LEN_MAPPING = 0x2C
BTN_CODE_LEN = 11
PAIR_M1M2 = 0x08

TARGET_USAGE = 0x41          # HID keyboard usage for F8
T_KB = 0x02
OFFSET = {0x01: 1, 0x02: 2, 0x03: 4, 0x05: 3}

CODE_KB_F8 = (T_KB, 0x0A)    # driver's KB_F8 value
CODE_KB_M1 = (T_KB, 0x8F)    # G-Helper BindM1 "02-8F"
CODE_KB_M2 = (T_KB, 0x8E)    # G-Helper BindM2 "02-8E"
CODE_NONE = (0x00, 0x00)


def entry(code):
    out = bytearray(BTN_CODE_LEN)
    btype, value = code
    if btype:
        out[0] = btype
        out[OFFSET[btype]] = value
    return bytes(out)


def mapping_packet(first, first_sec, second, second_sec):
    """Offsets follow G-Helper: 5=left primary, 16=left secondary,
    27=right primary, 38=right secondary."""
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    buf[1] = CODE_PAGE
    buf[2] = CMD_SET_MAPPING
    buf[3] = PAIR_M1M2
    buf[4] = LEN_MAPPING
    buf[5:16] = entry(first)
    buf[16:27] = entry(first_sec)
    buf[27:38] = entry(second)
    buf[38:49] = entry(second_sec)
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


def capture(nodes, seconds, prompt):
    fds, names = [], []
    for num, node in sorted(nodes.items()):
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
            names.append(f"1.{num}")
        except OSError:
            pass
    print(f"    >>> {prompt} ({seconds}s)...")
    out = {n: [] for n in names}
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            ready, _, _ = select.select(fds, [], [], max(0, deadline - time.time()))
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except (BlockingIOError, OSError):
                    continue
                if data:
                    out[names[fds.index(fd)]].append(bytes(data))
    finally:
        for fd in fds:
            os.close(fd)
    return out


def report(per_iface):
    hit = False
    for name, reports in sorted(per_iface.items()):
        if not reports:
            continue
        found = any(TARGET_USAGE in r for r in reports)
        hit = hit or found
        first = " ".join(f"{b:02x}" for b in reports[0][:8])
        mark = "   <- F8 (0x41)" if found else ""
        print(f"      [{name}] {len(reports)} reports, first: {first}{mark}")
    if not any(per_iface.values()):
        print("      (no reports)")
    print(f"      => {'F8 SEEN' if hit else 'no F8'}\n")
    return hit


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    if 2 not in nodes:
        sys.exit("No ROG Ally X config interface found.")

    m1_p, m2_p = sysfs("btn_m1/remap"), sysfs("btn_m2/remap")
    orig_m1 = read_attr(m1_p) if m1_p else None
    orig_m2 = read_attr(m2_p) if m2_p else None

    print(f"btn_m1/remap = {orig_m1}")
    print(f"btn_m2/remap = {orig_m2}\n")

    try:
        # ---- stage 1: which paddle does btn_m1 actually control? --------
        print("[1] SLOT ORDER - remap btn_m1 only, then press BOTH paddles")
        if not m1_p:
            print("    btn_m1/remap missing - skipping\n")
            m1_hit = m2_hit = False
        else:
            write_attr(m1_p, "KB_F8")
            print(f"    btn_m1/remap now reads: {read_attr(m1_p)}")
            time.sleep(0.4)
            print("    -- press M1 --")
            m1_hit = report(capture(nodes, 5, "press M1 a few times"))
            print("    -- press M2 --")
            m2_hit = report(capture(nodes, 5, "press M2 a few times"))

            if m2_hit and not m1_hit:
                print("    *** SLOTS ARE SWAPPED: btn_m1 programmed M2 ***")
                print("    Our driver's is_first for ALLY_BTN_M1 is inverted.\n")
            elif m1_hit and not m2_hit:
                print("    *** Driver is correct: btn_m1 programmed M1 ***\n")
            elif m1_hit and m2_hit:
                print("    *** Both paddles emit F8 - they are fused ***\n")
            else:
                print("    Neither paddle changed - the write did not land.\n")
            write_attr(m1_p, orig_m1 or "KB_M1")
            time.sleep(0.3)

        # ---- stage 2: does the secondary slot matter? -------------------
        print("[2] SECONDARY SLOT - G-Helper fills it, our driver zeroes it")
        print("    Writing both slots the way G-Helper does, F8 on the left")
        print("    slot and the stock vendor code in both secondary slots.\n")
        fd = os.open(nodes[2], os.O_RDWR)
        try:
            pkt = mapping_packet(CODE_KB_F8, CODE_KB_M2,
                                 CODE_KB_M1, CODE_KB_M1)
            print("    packet: " + " ".join(f"{b:02x}" for b in pkt[:16]) + " ...")
            fcntl.ioctl(fd, HIDIOCSFEATURE(REPORT_SIZE), bytearray(pkt))
            time.sleep(0.4)
            print("    -- press M1 --")
            report(capture(nodes, 5, "press M1 a few times"))
            print("    -- press M2 --")
            report(capture(nodes, 5, "press M2 a few times"))

            # restore both slots the G-Helper way: left=M2, right=M1
            restore = mapping_packet(CODE_KB_M2, CODE_KB_M2,
                                     CODE_KB_M1, CODE_KB_M1)
            fcntl.ioctl(fd, HIDIOCSFEATURE(REPORT_SIZE), bytearray(restore))
            print("    stock binding rewritten (left=M2, right=M1)")
        finally:
            os.close(fd)
    finally:
        print("\n[R] RESTORE")
        for p, orig, name in ((m1_p, orig_m1, "btn_m1"), (m2_p, orig_m2, "btn_m2")):
            if p and orig:
                write_attr(p, orig)
                print(f"    {name}/remap -> {read_attr(p)}")

    print("""
How to read this:
  [1] F8 on M2 only
      -> the slots are swapped. Our get_button_pair_info() has ALLY_BTN_M1 and
         ALLY_BTN_M2 backwards, every previous paddle result was measuring the
         wrong button, and the fix is two lines.
  [1] F8 on M1 only
      -> our driver is right and the swap theory is dead.
  [1] nothing, [2] F8 appears
      -> the secondary slot is what unlocks the paddles, and our driver zeroing
         it is the bug.
  [1] and [2] both nothing
      -> the paddles really are held by device state, and the G-Helper source
         is the place to keep reading.
""")


if __name__ == "__main__":
    main()
