import os, sys, fcntl, time

HIDIOCSFEATURE_64 = 0xC0404806

def test_sequence(device_path):
    print(f'Opening {device_path}...')
    fd = os.open(device_path, os.O_RDWR)
    
    colors = [
        (255, 0, 0),   # Red
        (0, 255, 0),   # Green
        (0, 0, 255),   # Blue
        (255, 255, 0), # Yellow
        (0, 255, 255), # Cyan
        (255, 0, 255)  # Magenta
    ]

    print('Sending fast updates without delays...')
    for _ in range(10): # Loop a few times to simulate dragging slider
        for r, g, b in colors:
            config_buf = bytearray([0x5a, 0xb3, 0x00, 0x00, r, g, b] + [0]*57)
            set_buf = bytearray([0x5a, 0xb5] + [0]*62)
            apply_buf = bytearray([0x5a, 0xb4] + [0]*62)
            
            try:
                # Fire them instantly back-to-back like the kernel driver
                fcntl.ioctl(fd, HIDIOCSFEATURE_64, config_buf)
                fcntl.ioctl(fd, HIDIOCSFEATURE_64, set_buf)
                fcntl.ioctl(fd, HIDIOCSFEATURE_64, apply_buf)
            except OSError:
                pass
            
            # Short wait before next color update (simulating 30ms throttle)
            time.sleep(0.03)

    # Clean up and reset with a proper delayed apply
    time.sleep(1)
    config_buf = bytearray([0x5a, 0xb3, 0x00, 0x00, 255, 0, 0] + [0]*57)
    fcntl.ioctl(fd, HIDIOCSFEATURE_64, config_buf)
    time.sleep(0.05)
    fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb5] + [0]*62))
    time.sleep(0.05)
    fcntl.ioctl(fd, HIDIOCSFEATURE_64, bytearray([0x5a, 0xb4] + [0]*62))

    os.close(fd)
    print('\nDone.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(1)
    test_sequence(sys.argv[1])
