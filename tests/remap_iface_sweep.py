#!/usr/bin/env python3
# remap_iface_sweep.py - where does CMD_SET_MAPPING actually have to go, and
# what does the MCU say about it?
#
# remap_check_ready.py ruled out the missing check-ready handshake: the mapping
# packet is ignored with or without it. But that run left two things unchecked:
#
#   1. We never READ BACK the MCU's response to the mapping write. A successful
#      HIDIOCSFEATURE only means the USB control transfer completed. The device
#      demonstrably returns a response buffer (check-ready echoed 5a d1 0a 01),
#      so there is a status here we have not looked at.
#
#   2. We only ever wrote to interface 1.2. The Ally X has an interface the
#      original Ally does not, and Nero reports remap works on the original.
#      If gamepad config lands elsewhere on this model, that is the difference.
#
# Three passes:
#   [1] DISCOVERY  - which interfaces answer the 0xD1 code page at all
#   [2] CONTROL    - compare the MCU response to a setter we KNOW works
#                    (vibration intensity, 0x06) against the mapping setter
#                    (0x02) on the same interface. Divergence = rejection.
#   [3] SWEEP      - write the mapping packet to each responding interface and
#                    watch whether the paddle changes what it emits.
#
# Passes 1 and 2 are read-mostly and need no interaction. Pass 3 asks you to
# press M1 once per candidate interface, and restores the stock M1/M2 binding
# on every interface it wrote to.
#
# Usage: sudo python3 remap_iface_sweep.py

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
CMD_CHECK_READY = 0x0A

# Read-only capability queries, used as controls. These change no state, so we
# get to see what a well-formed response looks like without touching settings.
# Names are from the Armoury Crate enums in gamepad-protocol.md.
CAP_QUERIES = [
    (0x0A, "CheckCustomButtonStatus"),
    (0x0C, "CheckXboxControllerStatusSupport"),
    (0x10, "CheckTurboSupport"),
    (0x17, "CheckAntiDeadzoneSupport"),
    (0x1A, "CheckButtonToTriggerSupport"),
]
LEN_MAPPING = 0x2C
BTN_CODE_LEN = 11
PAIR_M1M2 = 0x08

T_PAD, T_KB, T_MOUSE, T_MEDIA = 0x01, 0x02, 0x03, 0x05
OFFSET = {T_PAD: 1, T_KB: 2, T_MOUSE: 4, T_MEDIA: 3}

CODE_MEDIA_VOL_UP = (T_MEDIA, 0x03)
CODE_KB_M1 = (T_KB, 0x8F)
CODE_KB_M2 = (T_KB, 0x8E)
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


def simple_cmd(cmd, payload=b""):
    """The ordinary framing: 5A D1 <cmd> <len> <payload...>"""
    buf = bytearray(REPORT_SIZE)
    buf[0] = REPORT_ID
    buf[1] = CODE_PAGE
    buf[2] = cmd
    buf[3] = len(payload)
    buf[4:4 + len(payload)] = payload
    return bytes(buf)


def ally_interfaces():
    """Map USB interface number -> hidraw node for the Ally X."""
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


def set_feature(fd, payload):
    buf = bytearray(payload)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)


def get_feature(fd, length=REPORT_SIZE):
    buf = bytearray(length)
    buf[0] = REPORT_ID
    fcntl.ioctl(fd, HIDIOCGFEATURE(length), buf)
    return bytes(buf)


def hexdump(data, n=16):
    return " ".join(f"{b:02x}" for b in data[:n])


def try_exchange(node, packet, label):
    """SET then GET on one interface. Returns the response or None."""
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        print(f"    {label}: open failed ({e.strerror})")
        return None
    try:
        try:
            set_feature(fd, packet)
        except OSError as e:
            print(f"    {label}: SET rejected ({e.strerror})")
            return None
        time.sleep(0.05)
        try:
            resp = get_feature(fd)
        except OSError as e:
            print(f"    {label}: SET ok, GET failed ({e.strerror})")
            return b""
        print(f"    {label}: SET ok, GET -> {hexdump(resp)}")
        return resp
    finally:
        os.close(fd)


def watch_all(nodes, seconds, label):
    """Watch every interface at once for HID input reports."""
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
                src = names[fds.index(fd)]
                line = f"[{src}] {hexdump(data, 8)}"
                if line not in seen:
                    print(f"    {line}")
                seen.append(line)
    finally:
        for fd in fds:
            os.close(fd)
    if not seen:
        print("    (no reports seen)")
    print(f"  == {label} ==\n")
    return seen


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    nodes = ally_interfaces()
    if not nodes:
        sys.exit("No ROG Ally X (0b05:1b4c) hidraw nodes found.")
    print("Ally X interfaces:")
    for num, node in sorted(nodes.items()):
        print(f"  1.{num} -> {node}")
    print()

    # ---- pass 1: who speaks the 0xD1 code page? -------------------------
    print("[1] DISCOVERY - check-ready (0x0A) on every interface")
    speaks = {}
    for num, node in sorted(nodes.items()):
        resp = try_exchange(node, simple_cmd(CMD_CHECK_READY, b"\x01"), f"1.{num}")
        if resp and len(resp) > 2 and resp[1] == CODE_PAGE and resp[2] == CMD_CHECK_READY:
            speaks[num] = node
    print(f"\n  interfaces speaking 0xD1: {sorted(speaks) or 'NONE'}\n")

    if not speaks:
        sys.exit("Nothing answered the config code page - cannot continue.")

    # ---- pass 2: capability queries vs the mapping setter ---------------
    print("[2] CONTROL - read-only capability queries vs the mapping setter")
    print("    These queries change nothing. 0x0A is especially interesting:")
    print("    our driver treats it as 'check ready', but the Armoury Crate")
    print("    enum calls it CheckCustomButtonStatus - so its full response may")
    print("    say whether custom button mapping is even enabled.\n")
    for num, node in sorted(speaks.items()):
        print(f"  interface 1.{num}:")
        for cmd, name in CAP_QUERIES:
            try_exchange(node, simple_cmd(cmd, b"\x01"), f"      0x{cmd:02x} {name}")
        try_exchange(node, mapping_packet(PAIR_M1M2, CODE_MEDIA_VOL_UP, CODE_KB_M2),
                     "      0x02 SetUserDefButton")
    print("""
    If the queries echo cleanly and 0x02 comes back different (or zeroed), the
    MCU is rejecting the mapping subopcode rather than silently ignoring it.
""")

    # ---- pass 3: does the write land on any interface? ------------------
    print("[3] SWEEP - write the mapping to each interface, watch the paddle")
    print("    baseline first, for comparison:")
    watch_all(nodes, 5, "baseline")

    written = []
    for num, node in sorted(speaks.items()):
        print(f"  --- mapping written to interface 1.{num} ---")
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            print(f"    open failed ({e.strerror})\n")
            continue
        try:
            set_feature(fd, simple_cmd(CMD_CHECK_READY, b"\x01"))
            get_feature(fd)
            set_feature(fd, mapping_packet(PAIR_M1M2, CODE_MEDIA_VOL_UP, CODE_KB_M2))
            written.append(node)
        except OSError as e:
            print(f"    write failed ({e.strerror})\n")
            os.close(fd)
            continue
        os.close(fd)
        time.sleep(0.3)
        watch_all(nodes, 5, f"after write to 1.{num}")

    # ---- restore --------------------------------------------------------
    print("[R] RESTORE - stock M1/M2 binding on every interface written")
    for node in written:
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
            set_feature(fd, simple_cmd(CMD_CHECK_READY, b"\x01"))
            get_feature(fd)
            set_feature(fd, mapping_packet(PAIR_M1M2, CODE_KB_M1, CODE_KB_M2))
            os.close(fd)
            print(f"    restored via {node}")
        except OSError as e:
            print(f"    restore failed on {node} ({e.strerror})")
    print()
    watch_all(nodes, 5, "after restore")

    print("""
How to read this:
  Pass 3 shows the paddle changing after writing to some interface
      -> that is the interface gamepad config belongs on; the driver is simply
         sending to the wrong one on the Ally X.
  Pass 2 shows 0x06 echoing but 0x02 coming back different
      -> the firmware is actively rejecting the mapping subopcode; the next
         question is what precondition it wants.
  Everything identical everywhere
      -> the mapping table is not what governs this paddle on the Ally X. The
         remaining suspect is the persisted Armoury Crate "use as secondary
         function" routing, which sends both paddles to the vendor 5A A5 code
         on the config interface regardless of assigned keys.
""")


if __name__ == "__main__":
    main()
