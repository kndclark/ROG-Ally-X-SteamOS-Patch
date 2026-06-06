import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806

def calc_hardware_level(br):
    if br == 0: return 0
    elif br <= 85: return 1
    elif br <= 170: return 2
    else: return 3

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    print('Simulating dragging slider up from 0 to 255...')
    for br in range(1, 256, 2):
        level = calc_hardware_level(br)
        
        # Software scaling (like led_mc_calc_color_components)
        r = (255 * br) // 255
        g = 0
        b = 0

        bright_buf = bytearray([0x5d, 0xba, 0xc5, 0xc4, level] + [0]*59)
        config_buf = bytearray([0x5a, 0xb3, 0x00, 0x00, r, g, b] + [0]*57)
        
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bright_buf)
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, config_buf)
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb5] + [0]*62))
            fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb4] + [0]*62))
        except OSError: pass
        time.sleep(0.03)

    print('Simulating dragging slider down from 255 to 0...')
    for br in range(255, -1, -2):
        level = calc_hardware_level(br)
        r = (255 * br) // 255
        g = 0
        b = 0
        bright_buf = bytearray([0x5d, 0xba, 0xc5, 0xc4, level] + [0]*59)
        config_buf = bytearray([0x5a, 0xb3, 0x00, 0x00, r, g, b] + [0]*57)
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
