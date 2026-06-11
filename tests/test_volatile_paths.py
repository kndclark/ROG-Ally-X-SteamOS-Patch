"""
Volatile LED path probe — NO persistent commits.

Deliberately NEVER sends 0x5A 0xB5 (SET) or 0x5A 0xB4 (APPLY), and never
sends the 0x5D brightness report. Working theory: B5/B4 are save-to-MCU-flash
commits (the stock hid-asus-ally driver only sends them once, at suspend) and
hammering them is what progressively degrades the MCU.

Phase 1: B3-only static red       -> does a bare config take effect live?
Phase 2: 5A D1 08 0C direct zones -> stock driver's volatile per-zone packet
                                     (Z1 red, Z2 green, Z3 blue, Z4 yellow)

The final zone colors are left lit as persistent evidence.
"""
import os, sys, fcntl, time

def HIDIOCSFEATURE(size):
    # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, size)
    return (3 << 30) | (size << 16) | (0x48 << 8) | 0x06

DEV = sys.argv[1] if len(sys.argv) > 1 else '/dev/hidraw3'
fd = os.open(DEV, os.O_RDWR)

def send(data, pad=64):
    buf = bytearray(data)
    if pad and len(buf) < pad:
        buf += bytearray(pad - len(buf))
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)

print(f"Opened {DEV}")

print("Phase 1: B3 static red, NO commit (B5/B4 withheld)...")
send([0x5a, 0xb3, 0x00, 0x00, 0xff, 0x00, 0x00])
print("  sent. Holding 4s — LEDs should be solid red IF bare B3 applies live.")
time.sleep(4)

print("Phase 2: direct volatile zone set (5A D1 08 0C + 4x RGB)...")
# Same 16-byte packet the stock hid-asus-ally ally_rgb_do_work() sends.
pkt = [0x5a, 0xd1, 0x08, 0x0c,
       0xff, 0x00, 0x00,   # zone 1: red
       0x00, 0xff, 0x00,   # zone 2: green
       0x00, 0x00, 0xff,   # zone 3: blue
       0xff, 0xff, 0x00]   # zone 4: yellow
# repeat a few times in case an active aura animation repaints over it
for i in range(5):
    send(pkt, pad=16)
    time.sleep(0.2)
print("  sent 5x. Zone colors left lit as evidence.")

os.close(fd)
print("Done. Report what the LEDs show now.")
