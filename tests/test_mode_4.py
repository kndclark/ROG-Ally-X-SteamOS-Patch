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

def set_color_mode4(fd, r, g, b):
    print(f'Setting Mode 4 (Static) Color R:{r} G:{g} B:{b}')
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, 0x04, 0x00, r, g, b]
    set_buf = [FEATURE_KBD_REPORT_ID, 0xb5]
    apply_buf = [FEATURE_KBD_REPORT_ID, 0xb4]
    send_feature_report(fd, config_buf, 'Config')
    time.sleep(0.05)
    send_feature_report(fd, set_buf, 'Set')
    time.sleep(0.05)
    send_feature_report(fd, apply_buf, 'Apply')
    time.sleep(0.05)

def set_brightness(fd, level):
    print(f'Setting Brightness to {level}')
    bright_buf = [FEATURE_KBD_REPORT_ID, 0xba, level]
    set_buf = [FEATURE_KBD_REPORT_ID, 0xb5]
    apply_buf = [FEATURE_KBD_REPORT_ID, 0xb4]
    send_feature_report(fd, bright_buf, 'Brightness')
    time.sleep(0.05)
    send_feature_report(fd, set_buf, 'Set')
    time.sleep(0.05)
    send_feature_report(fd, apply_buf, 'Apply')
    time.sleep(0.05)

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    set_color_mode4(fd, 0, 255, 0) # Green
    
    for level in [0, 1, 2, 3, 2, 1, 0, 3]:
        set_brightness(fd, level)
        time.sleep(0.5)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
