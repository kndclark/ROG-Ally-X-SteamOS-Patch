import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806
FEATURE_KBD_REPORT_ID = 0x5a

def send_feature_report(fd, data, desc=''):
    padded = bytearray(data) + bytearray(64 - len(data))
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_64, padded)
        print(f'Sent {desc}')
    except OSError as e:
        print(f'Error: {e}')

def apply_sequence(fd):
    send_feature_report(fd, [FEATURE_KBD_REPORT_ID, 0xb5], 'Set')
    time.sleep(0.05)
    send_feature_report(fd, [FEATURE_KBD_REPORT_ID, 0xb4], 'Apply')
    time.sleep(0.05)

def set_right_stick(fd, r, g, b):
    print(f'\nSetting Right Stick (0xb3) to R:{r} G:{g} B:{b}')
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, 0x04, 0x00, r, g, b]
    send_feature_report(fd, config_buf, 'Config 0xb3')
    time.sleep(0.05)
    apply_sequence(fd)

def set_left_stick(fd, r, g, b):
    print(f'\nSetting Left Stick (0xba) to R:{r} G:{g} B:{b}')
    config_buf = [FEATURE_KBD_REPORT_ID, 0xba, 0x04, 0x00, r, g, b]
    send_feature_report(fd, config_buf, 'Config 0xba')
    time.sleep(0.05)
    apply_sequence(fd)

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    # Try to rescue the MCU state by sending an Apply first
    apply_sequence(fd)
    
    set_right_stick(fd, 255, 0, 0) # Red
    time.sleep(2)
    
    set_left_stick(fd, 0, 0, 255) # Blue
    time.sleep(2)

    set_right_stick(fd, 0, 255, 0) # Green
    time.sleep(2)
    
    set_left_stick(fd, 255, 255, 0) # Yellow
    time.sleep(2)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
