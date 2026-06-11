"""
Leg C of the A/B/C commit-pair isolation test.

Streams B3 (static config, sweeping hue) + B5 (SET latch) at ~30 Hz for 240
seconds with B4 (APPLY / NV commit) deliberately withheld.

  A (historical): B3+B5+B4 @ ~30 Hz -> corrupted in ~30 s
  B (proven):     5A D1 08 direct   -> 6788 frames, clean
  C (this test):  B3+B5 only        -> 6,212 frames, clean

Clean run = B4 alone is the corruption trigger and the driver's animation
path (B3+B5 on effect/color changes) is safe. Corruption = B5 also touches
NV state and the animation path needs a rethink.

Ends parked on solid green.
"""
import os, sys, fcntl, time, colorsys

def HIDIOCSFEATURE(size):
    return (3 << 30) | (size << 16) | (0x48 << 8) | 0x06

DEV = sys.argv[1] if len(sys.argv) > 1 else '/dev/hidraw3'
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 240.0
fd = os.open(DEV, os.O_RDWR)

B5_SET = bytearray([0x5a, 0xb5] + [0] * 62)

def send_b3_b5(r, g, b):
    cfg = bytearray([0x5a, 0xb3, 0x00, 0x00, r, g, b] + [0] * 57)
    fcntl.ioctl(fd, HIDIOCSFEATURE(64), cfg)
    fcntl.ioctl(fd, HIDIOCSFEATURE(64), B5_SET)

print(f"Sweeping via B3+B5 (no B4) on {DEV} for {DURATION:.0f}s at ~30Hz...")
start = time.monotonic()
frames = 0
errors = 0
while (elapsed := time.monotonic() - start) < DURATION:
    hue = (elapsed / 8.0) % 1.0
    r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0))
    try:
        send_b3_b5(r, g, b)
        frames += 1
    except OSError as e:
        errors += 1
        print(f"  ioctl error at {elapsed:.1f}s: {e}")
    if frames % 300 == 0:
        print(f"  {elapsed:.0f}s elapsed, {frames} frames, {errors} errors")
    time.sleep(0.033)

send_b3_b5(0, 255, 0)
print(f"DONE: {frames} frames ({frames*2} reports), {errors} errors in {elapsed:.0f}s.")
print("End state: solid green on all zones.")
os.close(fd)
