#!/usr/bin/env python3
"""Analyze inter-arrival gaps of gamepad input completions in a usbmon capture.

Selects successful interrupt-IN *completions with data* for device 005 on a given
endpoint, and reports the cadence. A clean ~1 ms stream during continuous stick
movement means no drops; input frames lost at the URB layer appear as anomalous
large gaps amid the otherwise-steady cadence.

Usage: usbmon_gaps.py <usbmon_file> [endpoint=7] [anomaly_ms=3.0]
"""
import re
import sys


def analyze(path, ep="7", dev="005", anomaly_ms=3.0):
    # line: <tag> <ts_us> C Ii:1:<dev>:<ep> <status>:<interval> <len> = <data>
    pat = re.compile(rf'\s(\d+)\s+C\s+\S+:{dev}:{ep}\s+(-?\d+):\d+\s+(\d+)')
    prev = None
    count = 0
    first = last = None
    gaps = []
    with open(path) as f:
        for line in f:
            m = pat.search(line)
            if not m:
                continue
            ts, status, length = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if status != 0 or length <= 0:      # only delivered data frames
                continue
            count += 1
            first = ts if first is None else first
            last = ts
            if prev is not None:
                g = (ts - prev) / 1000.0        # ms
                if g >= 0:
                    gaps.append(g)
            prev = ts
    if count < 2:
        print(f"{path}: only {count} data completions on ep{ep} - insufficient (did the stick move?)")
        return
    span = (last - first) / 1e6
    fast = [g for g in gaps if g < 1.8]              # normal ~1ms cadence
    missed = [g for g in gaps if 1.8 <= g < 8.0]     # 1-7 dropped frames = drop signature
    pause = [g for g in gaps if g >= 8.0]            # movement pauses (excluded from drop-rate)
    active = len(fast) + len(missed)
    drop_rate = (len(missed) / active * 100) if active else 0.0
    anomalies = sorted((g for g in gaps if g > anomaly_ms), reverse=True)
    print(f"{path}")
    print(f"  delivered pad frames on ep{ep}: {count}  over {span:.1f}s  (~{count/span:.0f}/s)")
    print(f"  inter-frame gap ms: mean={sum(gaps)/len(gaps):.2f}  max={max(gaps):.2f}")
    print(f"  buckets: normal<1.8ms={len(fast)}  missed 1.8-8ms={len(missed)}  pause>8ms={len(pause)}")
    print(f"  active drop-rate (missed/(normal+missed)) = {drop_rate:.2f}%")
    print(f"  anomalous gaps > {anomaly_ms}ms (candidate drops): {len(anomalies)}"
          + (f"  largest: {[round(g, 1) for g in anomalies[:12]]}" if anomalies else ""))


if __name__ == "__main__":
    a = sys.argv
    analyze(a[1] if len(a) > 1 else "/tmp/usbmon_stick.txt",
            ep=a[2] if len(a) > 2 else "7",
            dev=a[3] if len(a) > 3 else "005",
            anomaly_ms=float(a[4]) if len(a) > 4 else 3.0)
