import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806
FEATURE_KBD_REPORT_ID = 0x5a

def send_feature_report(fd, data):
    padded = bytearray(data) + bytearray(64 - len(data))
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_64, padded)
    except OSError as e:
        pass

def apply_sequence(fd):
    send_feature_report(fd, [FEATURE_KBD_REPORT_ID, 0xb5])
    time.sleep(0.05)
    send_feature_report(fd, [FEATURE_KBD_REPORT_ID, 0xb4])
    time.sleep(0.05)

def set_zone(fd, zone_id, r, g, b):
    print(f'Setting Zone {zone_id} to R:{r} G:{g} B:{b}')
    # buf[2] is Zone, buf[3] is Mode (0 = Static)
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, zone_id, 0x00, r, g, b]
    send_feature_report(fd, config_buf)
    time.sleep(0.05)
    apply_sequence(fd)

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    apply_sequence(fd)
    
    for zone in range(8):
        # We'll use White to make it very obvious
        set_zone(fd, zone, 255, 255, 255)
        time.sleep(2)
        # Turn it off before moving to the next
        set_zone(fd, zone, 0, 0, 0)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
