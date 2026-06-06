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

def test_offset(fd, offset):
    print(f'Testing Offset {offset} with White Color...')
    # Base packet: Command 0xb3, Mode 4, Speed 0
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, 0x04, 0x00] + [0]*60
    
    # Inject White (255, 255, 255) at the given offset
    if offset + 2 < 60:
        config_buf[offset] = 255
        config_buf[offset+1] = 255
        config_buf[offset+2] = 255

    send_feature_report(fd, config_buf[:64])
    time.sleep(0.05)
    apply_sequence(fd)

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    apply_sequence(fd)
    
    # We know offset 4,5,6 is the right stick. Let's sweep offsets 7 to 30.
    for offset in range(4, 30):
        test_offset(fd, offset)
        time.sleep(2)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
