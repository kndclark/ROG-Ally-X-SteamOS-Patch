"""
Sustained stress test of the volatile direct-zone LED path (5A D1 08 0C).

Sends a smooth full-spectrum hue sweep to all 4 zones at ~30 Hz for 240
seconds (~7200 packets) — the same cadence the kernel driver uses when a
SteamOS slider is dragged, but with ZERO persistent commits (no B3/B5/B4,
no 0x5D). This is the direct comparison against the old throttled control
test (B3+B5+B4 each frame), which corrupted after ~30 seconds.

Ends by setting the 4-distinct-color evidence state (Z1 red, Z2 green,
Z3 blue, Z4 yellow) so end-state integrity is itself a check.
"""
import os, sys, fcntl, time, colorsys

def HIDIOCSFEATURE(size):
    return (3 << 30) | (size << 16) | (0x48 << 8) | 0x06

DEV = sys.argv[1] if len(sys.argv) > 1 else '/dev/hidraw3'
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 240.0
fd = os.open(DEV, os.O_RDWR)

def send_zones(rgb4):
    pkt = bytearray([0x5a, 0xd1, 0x08, 0x0c])
    for r, g, b in rgb4:
        pkt += bytes((r, g, b))
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(pkt)), pkt)

print(f"Sweeping all zones via 5A D1 08 0C on {DEV} for {DURATION:.0f}s at ~30Hz...")
start = time.monotonic()
frames = 0
errors = 0
while (elapsed := time.monotonic() - start) < DURATION:
    hue = (elapsed / 8.0) % 1.0          # full spectrum every 8 seconds
    r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0))
    try:
        send_zones([(r, g, b)] * 4)
        frames += 1
    except OSError as e:
        errors += 1
        print(f"  ioctl error at {elapsed:.1f}s: {e}")
    if frames % 300 == 0:
        print(f"  {elapsed:.0f}s elapsed, {frames} frames, {errors} errors")
    time.sleep(0.033)

# evidence end-state: distinct zone colors
send_zones([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)])
print(f"DONE: {frames} frames sent, {errors} errors in {elapsed:.0f}s.")
print("End state: Z1 red, Z2 green, Z3 blue, Z4 yellow.")
os.close(fd)
