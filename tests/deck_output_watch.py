#!/usr/bin/env python3
# deck_output_watch.py - what does InputPlumber actually SEND to Steam?
#
# READ-ONLY. Opens one hidraw node for reading and writes nothing anywhere.
# InputPlumber is left running on purpose - stopping it destroys the very
# device this observes.
#
# Why this exists
# ---------------
# Every capture so far watched InputPlumber's INPUT. None watched its OUTPUT.
# InputPlumber emulates a Valve Steam Deck Controller (uhid, 28de:12fd) which
# Steam reads over hidraw; it has no evdev nodes at all. That device is the
# last unobserved link:
#
#   Ally MCU -> hid-asus -> evdev -> InputPlumber -> deck-uhid -> Steam
#                                                    ^^^^^^^^^ here
#
# The open question: holding CC emits KEY_F20 for 1-2 ms, InputPlumber's aly1
# map turns that into the Deck's "Keyboard" action, the controller test screen
# flashes the glyphs - but the on-screen keyboard never opens.
#
# Finding the button bits without knowing the report format
# --------------------------------------------------------
# A first version of this diffed whole reports against an idle sample and was
# worthless: the Deck report carries a sequence counter and live IMU data, so
# every report differs from every other and 1932 samples produced 1932
# "transitions".
#
# Instead: profile which byte offsets are STABLE while idle, then report only
# offsets that take a value during the press which never occurred at idle.
# A counter cycles through every value and the IMU bytes are noisy, so both are
# excluded automatically by the stability filter - no need to know the layout.
#
# Usage: sudo python3 deck_output_watch.py

import glob
import os
import select
import sys
import time

VALVE_HID = "0003:28DE:12FD.*"
# Offsets taking more than this many values at idle are treated as noise.
# 4 proved far too permissive: the Deck report is mostly zeros when untouched,
# so 59 of 64 offsets passed and analog drift from gripping the device during a
# long hold registered as button activity. A real button byte is CONSTANT while
# nothing is pressed, so 1 is the correct threshold.
STABLE_MAX = 1
BASELINE_SECS = 5


def deck_hidraw():
    for d in sorted(glob.glob("/sys/bus/hid/devices/" + VALVE_HID)):
        hr = glob.glob(os.path.join(d, "hidraw", "hidraw*"))
        if hr:
            return "/dev/" + os.path.basename(hr[0]), os.path.basename(d)
    return None, None


def flush(fd):
    """Drop anything already buffered so timings start at zero."""
    while True:
        r, _, _ = select.select([fd], [], [], 0)
        if not r:
            return
        try:
            if not os.read(fd, 4096):
                return
        except (BlockingIOError, OSError):
            return


def collect(fd, seconds, prompt):
    """Return [(ms, report)] for the window."""
    print(f"\n  >>> {prompt} ({seconds}s)...")
    flush(fd)
    out = []
    t0 = time.monotonic()
    end = t0 + seconds
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], max(0, end - time.monotonic()))
        if not r:
            continue
        try:
            data = os.read(fd, 512)
        except (BlockingIOError, OSError):
            continue
        if data:
            out.append(((time.monotonic() - t0) * 1000.0, bytes(data)))
    return out


def profile(samples):
    """{offset: set(values)} across all samples."""
    seen = {}
    for _, rep in samples:
        for i, b in enumerate(rep):
            seen.setdefault(i, set()).add(b)
    return seen


def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo")

    node, dev = deck_hidraw()
    if not node:
        sys.exit("No Valve 28de:12fd device found - is InputPlumber running?")
    print(f"InputPlumber output device: {dev}")
    print(f"hidraw node               : {node}")
    print("\nInputPlumber is left running; stopping it would destroy this device.")

    fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
    try:
        input(f"\n[baseline] Do NOT touch the device. Enter to profile idle... ")
        base = collect(fd, BASELINE_SECS, "measuring idle traffic")
        if not base:
            sys.exit("      No reports at all - cannot continue.")
        idle = profile(base)
        stable = {i: v for i, v in idle.items() if len(v) <= STABLE_MAX}
        print(f"      {len(base)} reports, {len(idle)} byte offsets")
        print(f"      {len(stable)} offsets stable at idle (<= {STABLE_MAX} values)")
        print(f"      stable offsets: {sorted(stable)[:24]}")

        for name, prompt, secs in (
            ("CC short", "tap CC once", 6),
            ("CC long", "press CC and HOLD ~4s, then release", 9),
            ("AC long", "press AC and HOLD ~4s, then release", 9),
        ):
            input(f"\n[{name}] Enter when ready... ")
            samples = collect(fd, secs, prompt)
            print(f"      {len(samples)} reports")

            # (offset, value) pairs never seen at idle, with first/last time.
            events = {}
            for ms, rep in samples:
                for i in stable:
                    if i < len(rep) and rep[i] not in stable[i]:
                        k = (i, rep[i])
                        if k not in events:
                            events[k] = [ms, ms]
                        else:
                            events[k][1] = ms

            if not events:
                print("      NO new value on any stable byte - nothing emitted")
                continue
            print("      byte  value  first      last       held")
            for (i, v), (first, last) in sorted(events.items(),
                                                key=lambda kv: kv[1][0]):
                print(f"      b{i:<4} 0x{v:02x}   {first:8.1f}   {last:8.1f}   "
                      f"{last - first:7.1f} ms")
    finally:
        os.close(fd)

    print("""
How to read this:
  CC long shows nothing
      -> InputPlumber never emits the action. Key duration in the driver is
         irrelevant; the problem is in the mapping.
  CC long shows a bit held for only a few ms
      -> emitted but momentary. Lengthening KEY_F20 in the driver becomes a
         justified, testable fix.
  CC long shows a bit held for the whole press
      -> Steam is being told correctly and is not acting. Not our bug.
  AC long is the control: we know that one now holds for the full press, so
  its row shows what a correct, sustained action looks like here.
""")


if __name__ == "__main__":
    main()
