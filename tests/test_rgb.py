import os
import sys
import fcntl
import time

HIDIOCSFEATURE_64 = 0xC0404806
FEATURE_KBD_REPORT_ID = 0x5a

def send_feature_report(fd, data, desc=''):
    padded = bytearray(data) + bytearray(64 - len(data))
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_64, padded)
        print(f'Sent {desc} ({len(data)} bytes)')
    except OSError as e:
        print(f'Error sending {desc}: {e}')

def apply_color(fd, r, g, b):
    # Config sequence
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, 0x00, 0x00, r, g, b]
    set_buf = [FEATURE_KBD_REPORT_ID, 0xb5]
    apply_buf = [FEATURE_KBD_REPORT_ID, 0xb4]
    
    send_feature_report(fd, config_buf, f'Config (R:{r} G:{g} B:{b})')
    time.sleep(0.05)
    send_feature_report(fd, set_buf, 'Set')
    time.sleep(0.05)
    send_feature_report(fd, apply_buf, 'Apply')
    time.sleep(0.05)

def apply_brightness(fd, level):
    # Brightness sequence
    bright_buf = [FEATURE_KBD_REPORT_ID, 0xba, level]
    set_buf = [FEATURE_KBD_REPORT_ID, 0xb5]
    apply_buf = [FEATURE_KBD_REPORT_ID, 0xb4]
    
    send_feature_report(fd, bright_buf, f'Brightness (Level:{level})')
    time.sleep(0.05)
    send_feature_report(fd, set_buf, 'Set')
    time.sleep(0.05)
    send_feature_report(fd, apply_buf, 'Apply')
    time.sleep(0.05)

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    try:
        fd = os.open(device_path, os.O_RDWR)
    except Exception as e:
        print(f'Failed to open device: {e}')
        sys.exit(1)
    
    print('\n--- Testing Color Sweep ---')
    for intensity in range(0, 255, 50):
        apply_color(fd, intensity, 0, 0)
        time.sleep(0.5)
        
    for intensity in range(0, 255, 50):
        apply_color(fd, 0, intensity, 0)
        time.sleep(0.5)
        
    print('\n--- Testing Brightness Sweep ---')
    # Valid levels: 0, 1, 2, 3
    for level in [0, 1, 2, 3, 2, 1, 0, 1, 2, 3]:
        apply_brightness(fd, level)
        time.sleep(0.5)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 test_rgb.py /dev/hidrawX')
        print('To find the device, try: ls -l /sys/class/hidraw/hidraw*/device/driver | grep asus')
        sys.exit(1)
    test_sequence(sys.argv[1])
