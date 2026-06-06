import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806
FEATURE_KBD_REPORT_ID = 0x5a

def send_feature_report(fd, data, desc=''):
    padded = bytearray(data) + bytearray(64 - len(data))
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_64, padded)
        print(f'   -> Sent {desc}')
    except OSError as e:
        print(f'   -> Error: {e}')

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    print('\n[1/4] Preparing Config Packet (Green, Mode 4)...')
    config_buf = [FEATURE_KBD_REPORT_ID, 0xb3, 0x04, 0x00, 0, 255, 0]
    time.sleep(2)
    send_feature_report(fd, config_buf, 'Config')
    
    print('\n[2/4] Sending Set Packet...')
    set_buf = [FEATURE_KBD_REPORT_ID, 0xb5]
    time.sleep(2)
    send_feature_report(fd, set_buf, 'Set')
    
    print('\n[3/4] Sending Apply Packet...')
    apply_buf = [FEATURE_KBD_REPORT_ID, 0xb4]
    time.sleep(2)
    send_feature_report(fd, apply_buf, 'Apply')
    
    print('\n[4/4] Sending Brightness Packet (Level 0)...')
    bright_buf = [FEATURE_KBD_REPORT_ID, 0xba, 0x00]
    time.sleep(2)
    send_feature_report(fd, bright_buf, 'Brightness')
    
    print('\n[5/4] Sending Set Packet...')
    time.sleep(2)
    send_feature_report(fd, set_buf, 'Set')
    
    print('\n[6/4] Sending Apply Packet...')
    time.sleep(2)
    send_feature_report(fd, apply_buf, 'Apply')

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
