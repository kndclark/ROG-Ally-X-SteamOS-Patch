# ROG Ally X LED Diagnostic Tests

This directory contains various diagnostic scripts used to reverse-engineer and troubleshoot the ROG Ally X LED subsystem.

## Usage
These scripts interact directly with the hardware via hidraw and bypass the kernel driver.
**You must unload the kernel driver before running most of these tests** to prevent conflicts:
```bash
sudo modprobe -r hid_asus_ally
sudo modprobe -r hid_asus
```

To run a test, specify the raw HID device path (usually /dev/hidraw2 or similar):
```bash
sudo python3 test_thresholds.py /dev/hidraw2
```

## Key Tests
- `test_thresholds.py`: Demonstrates the hardware brightness jumping bug by sweeping the slider.
- `test_flicker.py`: A dual-loop test to isolate software color scaling from hardware brightness scaling.
- `test_real_zones.py`: Sweeps through the 5 actual active LED zones on the Ally X.
- `sniff_ally_usb.py`: A utility to sniff raw USB traffic from the device using usbmon.
