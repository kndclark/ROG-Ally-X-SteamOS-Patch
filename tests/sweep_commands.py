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

def test_command(fd, cmd_byte):
    print(f'Testing Command 0x{cmd_byte:02x} with Blue Color...')
    config_buf = [FEATURE_KBD_REPORT_ID, cmd_byte, 0x04, 0x00, 0, 0, 255]
    send_feature_report(fd, config_buf)
    time.sleep(0.05)
    apply_sequence(fd)

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    apply_sequence(fd)
    
    for cmd in range(0xb0, 0xc0):
        if cmd in [0xb3, 0xb4, 0xb5]:
            continue
        test_command(fd, cmd)
        #time.sleep(2)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
