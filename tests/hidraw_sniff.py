#!/usr/bin/env python3
"""
hidraw_sniff.py - robust, read-only multi-node HID input sniffer for the ROG Ally.

Purpose
-------
Quantify whether keyboard (N-KEY) input reports get dropped, and compare
behaviour before vs after the usbhid_custom "skip poll when no input reports"
patch (PR62 in usbhid_custom/hid-core.c::usbhid_open).

The bug only manifests while a *feature-only* node's interrupt-IN URB is armed,
which happens on open(). So the controlled experiment is:
  - cat the feature-only (RGB) node to ARM the buggy URB, while
  - watching the keyboard node here for dropped reports.

Safety
------
Every hidraw node is opened O_RDONLY | O_NONBLOCK. This tool NEVER writes to the
device. It does not load, bind, or modify any driver.

Usage
-----
  sudo python3 hidraw_sniff.py list
  sudo python3 hidraw_sniff.py watch [--node hidraw3 ...] [--duration 30]
  sudo python3 hidraw_sniff.py trial --node hidraw3 --expect 50
"""

import argparse
import os
import select
import signal
import sys
import time

SYS_HIDRAW = "/sys/class/hidraw"

# HID short-item main-tag prefixes (bType=00). Size bits (low 2) vary; mask them.
MAIN_INPUT = 0x80
MAIN_OUTPUT = 0x90
MAIN_FEATURE = 0xB0
MAIN_MASK = 0xFC  # ignore the 2 size bits


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def _read_text(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError:
        return ""


def parse_main_items(desc):
    """Count Input/Output/Feature main items in a HID report descriptor.

    A node with input_items == 0 is exactly what PR62 skips polling for.
    """
    counts = {"input": 0, "output": 0, "feature": 0}
    i, n = 0, len(desc)
    while i < n:
        prefix = desc[i]
        if prefix == 0xFE:  # long item: [0xFE][dataSize][tag][data...]
            data_size = desc[i + 1] if i + 1 < n else 0
            i += 3 + data_size
            continue
        size_code = prefix & 0x03
        data_len = 4 if size_code == 3 else size_code
        tag = prefix & MAIN_MASK
        if tag == MAIN_INPUT:
            counts["input"] += 1
        elif tag == MAIN_OUTPUT:
            counts["output"] += 1
        elif tag == MAIN_FEATURE:
            counts["feature"] += 1
        i += 1 + data_len
    return counts


def discover():
    """Return a list of dicts describing every hidraw node."""
    nodes = []
    if not os.path.isdir(SYS_HIDRAW):
        return nodes
    for name in sorted(os.listdir(SYS_HIDRAW), key=lambda s: int(s[6:]) if s[6:].isdigit() else 0):
        base = os.path.join(SYS_HIDRAW, name, "device")
        uevent = _read_text(os.path.join(base, "uevent"))
        hid_name = ""
        for line in uevent.splitlines():
            if line.startswith("HID_NAME="):
                hid_name = line.split("=", 1)[1]
        # Resolve the USB interface (e.g. 1-2:1.1) from the realpath.
        iface = "?"
        real = os.path.realpath(base)
        for part in real.split("/"):
            if ":1." in part and "-" in part:
                iface = part
        counts = parse_main_items(_read_bytes(os.path.join(base, "report_descriptor")))
        nodes.append({
            "node": name,
            "dev": "/dev/" + name,
            "iface": iface,
            "name": hid_name or "(unknown)",
            "counts": counts,
            "feature_only": counts["input"] == 0,
        })
    return nodes


def cmd_list(_args):
    nodes = discover()
    if not nodes:
        print("No hidraw nodes found (run with sudo?).")
        return
    print(f"{'node':9} {'iface':10} {'in/out/feat':12} {'poll?':6} name")
    print("-" * 78)
    for d in nodes:
        c = d["counts"]
        io = f"{c['input']}/{c['output']}/{c['feature']}"
        poll = "SKIP" if d["feature_only"] else "poll"
        flag = "  <- feature-only (PR62 target)" if d["feature_only"] else ""
        print(f"{d['node']:9} {d['iface']:10} {io:12} {poll:6} {d['name']}{flag}")


def _open_nodes(node_names):
    """Open the given hidraw node names O_RDONLY|O_NONBLOCK. Returns {fd: meta}."""
    all_nodes = {d["node"]: d for d in discover()}
    fds = {}
    for name in node_names:
        meta = all_nodes.get(name)
        if meta is None:
            print(f"!! {name} not present, skipping")
            continue
        try:
            fd = os.open(meta["dev"], os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            print(f"!! cannot open {meta['dev']}: {e}")
            continue
        fds[fd] = {"name": name, "label": f"{name}({meta['iface']})",
                   "count": 0, "bytes": 0, "presses": 0, "releases": 0,
                   "first": None, "last": None, "max_gap": 0.0}
    return fds


def _drain_and_print(fds, label_width, quiet=False):
    """Read all ready fds once; update stats; print each report unless quiet."""
    rlist, _, _ = select.select(list(fds), [], [], 0.2)
    now = time.monotonic()
    for fd in rlist:
        try:
            data = os.read(fd, 256)
        except OSError:
            continue
        if not data:
            continue
        s = fds[fd]
        if s["last"] is not None:
            s["max_gap"] = max(s["max_gap"], now - s["last"])
        s["first"] = s["first"] if s["first"] is not None else now
        s["last"] = now
        s["count"] += 1
        s["bytes"] += len(data)
        # press = report carries a non-zero keycode after the report id; all-zero = release.
        if len(data) > 1 and any(data[1:]):
            s["presses"] += 1
            kind = "DOWN"
        else:
            s["releases"] += 1
            kind = "up  "
        if not quiet:
            hexs = " ".join(f"{b:02x}" for b in data)
            print(f"[{now:10.3f}] {s['label']:<{label_width}} {kind} ({len(data):2}B) {hexs}")


def _print_summary(fds):
    print("\n=== summary ===")
    print(f"{'node':22} {'reports':>8} {'press':>6} {'release':>8} {'max gap(s)':>11}")
    print("-" * 60)
    for s in sorted(fds.values(), key=lambda x: x["name"]):
        print(f"{s['label']:22} {s['count']:8} {s['presses']:6} {s['releases']:8} {s['max_gap']:11.3f}")


def cmd_watch(args):
    names = args.node if args.node else [d["node"] for d in discover()]
    fds = _open_nodes(names)
    if not fds:
        print("No nodes opened.")
        return
    width = max((len(s["label"]) for s in fds.values()), default=12)
    print(f"Watching {len(fds)} node(s). Ctrl-C to stop"
          + (f", auto-stop in {args.duration}s." if args.duration else "."))
    for s in fds.values():
        print(f"  - {s['label']}")
    print("-" * 60)
    print(">>> READY — nodes are open, PRESS NOW <<<", flush=True)
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    t0 = time.monotonic()
    while not stop["flag"]:
        _drain_and_print(fds, width, quiet=args.quiet)
        if args.duration and (time.monotonic() - t0) >= args.duration:
            break
    _print_summary(fds)


def cmd_trial(args):
    fds = _open_nodes([args.node])
    if not fds:
        print(f"Could not open {args.node}.")
        return
    s = next(iter(fds.values()))
    print(f"TRIAL on {s['label']} - expecting {args.expect} press events.")
    print("Press the target button SLOWLY and deliberately, ~1 per second.")
    print("Each physical press usually yields a press report + a release report.")
    print("Press Ctrl-C when you have done all "
          f"{args.expect} presses.\n")
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    while not stop["flag"]:
        _drain_and_print(fds, len(s["label"]))
    got = s["count"]
    print(f"\nReceived {got} reports for {args.expect} intended presses.")
    if args.expect:
        # press+release => ~2 reports/press; report both raw and pressed-pair view.
        pairs = got / 2.0
        drop = (args.expect - pairs) / args.expect * 100.0
        print(f"  ~{pairs:.1f} press/release pairs  =>  estimated drop: {drop:+.1f}%")
    print("  (Compare this number stock vs usbhid_custom under identical presses.)")


def main():
    if os.geteuid() != 0:
        print("Note: hidraw usually needs root. Re-run with sudo if nodes are empty.\n")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="enumerate hidraw nodes and flag feature-only ones")
    w = sub.add_parser("watch", help="live-watch nodes (default: all)")
    w.add_argument("--node", action="append", help="hidraw node name, e.g. hidraw3 (repeat for multiple)")
    w.add_argument("--duration", type=float, default=0, help="auto-stop after N seconds")
    w.add_argument("--quiet", action="store_true", help="suppress per-report lines; count only (for high-rate streams)")
    t = sub.add_parser("trial", help="counted press test on one node")
    t.add_argument("--node", required=True, help="hidraw node, e.g. hidraw3")
    t.add_argument("--expect", type=int, required=True, help="number of presses you will do")
    args = p.parse_args()
    {"list": cmd_list, "watch": cmd_watch, "trial": cmd_trial}.get(
        args.cmd or "list", cmd_list)(args)


if __name__ == "__main__":
    main()
