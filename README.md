# ROG Ally X LED Driver Patch

This repository contains a patched `hid-asus-ally` kernel module for the ASUS ROG Ally X, specifically optimized for SteamOS Game Mode.

## Features
- **Accurate Speed Mapping**: Maps the SteamOS LED speed slider (0-100) to calibrated hardware animation bins (13s, 9s, 5s pulses).
- **Animation Persistence**: Fixed a bug where brightness changes would reset animations (Rainbow/Chroma) to solid colors.
- **Hardware Brightness Mapping**: Implemented a 4-level hardware intensity mapping (Off, Low, Med, High) for autonomous animations.
- **Micro-animation Fixes**: Eliminated the "Red Pulse" artifact during Breathe effects.

## Prerequisites & Setup (SteamOS)

To build and install this module on SteamOS, you must first enable developer mode and unlock the filesystem.

### 1. Unlock Filesystem & Initialize Keys
Run these commands to allow installing system-level packages:

```bash
# Disable read-only mode
sudo steamos-readonly disable

# Initialize and populate pacman keys (required for package installation)
sudo pacman-key --init
sudo pacman-key --populate archlinux holo
```

### 2. Install Build Tools & Headers
Install the necessary developer packages:

```bash
# Install base development tools and current kernel headers
sudo pacman -S base-devel linux-neptune-618-headers
```

## Build & Install Instructions

```bash
cd ally_module

# 1. Clean previous builds
make clean

# 2. Build against current kernel headers
make -C /lib/modules/$(uname -r)/build M=$(pwd) modules

# 3. Compress the module
zstd -f hid-asus-ally.ko

# 4. Install to the drivers directory
sudo cp hid-asus-ally.ko.zst /lib/modules/$(uname -r)/kernel/drivers/hid/

# 5. Update dependency map and reload
sudo depmod -a
sudo modprobe -r hid_asus_ally
sudo modprobe hid_asus_ally

# 6. Verify logs
sudo dmesg | grep -i "ally"
```

## Credits
Based on the ASUS ROG HID driver by Luke Jones.