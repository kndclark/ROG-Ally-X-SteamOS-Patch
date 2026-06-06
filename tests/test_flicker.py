import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    # 1. Test Color ONLY
    print('Testing rapid Color updates (0xb3) only for 3 seconds...')
    for i in range(100):
        # Varying red slightly to simulate dragging slider
        r = 200 + (i % 55)
        config_buf = bytearray([0x5a, 0xb3, 0x00, 0x00, r, 0, 0] + [0]*57)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, config_buf)
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb5] + [0]*62))
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb4] + [0]*62))
        except OSError: pass
        time.sleep(0.03)
        
    print('Pausing...')
    time.sleep(1)

    # 2. Test Color + Brightness
    print('Testing rapid Color (0xb3) + Brightness (0x5d) updates for 3 seconds...')
    for i in range(100):
        r = 200 + (i % 55)
        config_buf = bytearray([0x5a, 0xb3, 0x00, 0x00, r, 0, 0] + [0]*57)
        bright_buf = bytearray([0x5d, 0xba, 0xc5, 0xc4, 0x03] + [0]*59)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bright_buf)
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, config_buf)
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb5] + [0]*62))
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb4] + [0]*62))
        except OSError: pass
        time.sleep(0.03)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
