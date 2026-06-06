import os, sys, fcntl, time, glob

HIDIOCSFEATURE_64 = 0xC0404806
FEATURE_KBD_REPORT_ID = 0x5a

def send_feature_report(fd, data):
    padded = bytearray(data) + bytearray(64 - len(data))
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_64, padded)
        return True
    except OSError as e:
        return False

def apply_sequence(fd):
    send_feature_report(fd, [FEATURE_KBD_REPORT_ID, 0xb5])
    time.sleep(0.05)
    send_feature_report(fd, [FEATURE_KBD_REPORT_ID, 0xb4])
    time.sleep(0.05)

def set_color(fd, r, g, b):
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, 0x04, 0x00, r, g, b]
    if send_feature_report(fd, config_buf):
        time.sleep(0.05)
        apply_sequence(fd)
        return True
    return False

def test_sequence():
    hidraw_devices = glob.glob('/dev/hidraw*')
    print(f'Found devices: {hidraw_devices}')
    
    for dev in hidraw_devices:
        print(f'\n--- Testing {dev} ---')
        try:
            fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            print(f'Cannot open {dev}: {e}')
            continue
            
        print('Sending White to this device...')
        if set_color(fd, 255, 255, 255):
            print('Command accepted!')
        else:
            print('Command rejected (not the right device interface).')
            
        time.sleep(2)
        
        # Turn it back to black/off so we can tell which one worked
        set_color(fd, 0, 0, 0)
        os.close(fd)

    print('\nDone.')

if __name__ == '__main__':
    test_sequence()
