import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806
FEATURE_KBD_REPORT_ID = 0x5a

def send_feature_report(fd, data, desc=''):
    padded = bytearray(data) + bytearray(64 - len(data))
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_64, padded)
        print(f'Sent {desc}')
    except OSError as e:
        pass

def test_mode(fd, mode, r, g, b):
    print(f'\n--- Testing Mode {mode} with Color R:{r} G:{g} B:{b} ---')
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, mode, 0x00, r, g, b]
    set_buf = [FEATURE_KBD_REPORT_ID, 0xb5]
    apply_buf = [FEATURE_KBD_REPORT_ID, 0xb4]
    
    send_feature_report(fd, config_buf, 'Config')
    time.sleep(0.05)
    send_feature_report(fd, set_buf, 'Set')
    time.sleep(0.05)
    send_feature_report(fd, apply_buf, 'Apply')

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    for mode in range(10):
        test_mode(fd, mode, 255, 0, 0)
        time.sleep(3)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 test_modes.py /dev/hidrawX')
        sys.exit(1)
    test_sequence(sys.argv[1])
