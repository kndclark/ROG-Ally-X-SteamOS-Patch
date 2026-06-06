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
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, 0x04, zone_id, r, g, b]
    send_feature_report(fd, config_buf)
    time.sleep(0.05)
    apply_sequence(fd)

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    # Turn off both (or everything) using zone 0 just in case
    print('Turning everything off...')
    set_zone(fd, 0, 0, 0, 0)
    set_zone(fd, 1, 0, 0, 0)
    set_zone(fd, 2, 0, 0, 0)
    time.sleep(1)

    print('\n--- Testing Zone 0 ---')
    set_zone(fd, 0, 255, 0, 0) # Red
    time.sleep(2)
    set_zone(fd, 0, 0, 0, 0)
    
    print('\n--- Testing Zone 1 ---')
    set_zone(fd, 1, 0, 255, 0) # Green
    time.sleep(2)
    set_zone(fd, 1, 0, 0, 0)
    
    print('\n--- Testing Zone 2 ---')
    set_zone(fd, 2, 0, 0, 255) # Blue
    time.sleep(2)
    set_zone(fd, 2, 0, 0, 0)

    print('\n--- Testing Zone 3 ---')
    set_zone(fd, 3, 255, 255, 0) # Yellow
    time.sleep(2)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
