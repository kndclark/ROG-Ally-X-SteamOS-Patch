import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    # 1. Set solid color (Zone 0, Mode 0, White)
    config_buf = [0x5a, 0xb3, 0x00, 0x00, 255, 255, 255] + [0]*57
    fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray(config_buf))
    time.sleep(0.05)
    fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb5] + [0]*62))
    time.sleep(0.05)
    fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb4] + [0]*62))
    print('Set to solid White.')
    time.sleep(2)

    # 2. Test hardware brightness commands
    for level in [0, 1, 2, 3]:
        print(f'Setting hardware brightness to level {level} (0x5D command)...')
        bright_buf = [0x5d, 0xba, 0xc5, 0xc4, level] + [0]*59
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray(bright_buf))
        except OSError as e:
            print(f'Error sending brightness: {e}')
        time.sleep(2)

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
