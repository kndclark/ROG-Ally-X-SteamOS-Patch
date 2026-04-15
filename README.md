# ROG Ally X Driver Patch

This repository contains a patched `hid-asus-ally` kernel module for the ASUS ROG Ally X, with specific fixes for LED RGB controls and vibration intensity in SteamOS Game Mode.

## Prerequisites

Before installing, ensure the following:

- **User deck password set**  
  SteamOS requires a user password for administrative (`sudo`) actions.  
  To set it, open a terminal and run:
  ```bash
  passwd
  ```

- **Git installed**  
  SteamOS does not always include Git by default. To install it:

  1. Disable the read-only filesystem:
     ```bash
     sudo steamos-readonly disable
     ```

  2. Initialize and populate the package manager keys:
     ```bash
     sudo pacman-key --init
     sudo pacman-key --populate archlinux
     ```

  3. Install Git:
     ```bash
     sudo pacman -S git
     ```
  4. Clone the repo:
     ```bash
     git clone https://github.com/kndclark/ally_module.git

> [!TIP]
> **Clone to persistent storage**: When SteamOS performs a system update, your filesystem and drivers are reset, but your `/home` directory will be preserved; it is recommended to clone this repo to that location.
---

  5. (Optional) Re-enable the read-only filesystem:
     ```bash
     sudo steamos-readonly enable
     ```

## Features
- **SteamOS Native Integration**: Added sysfs stubs that allow the SteamOS Game Mode LED settings to control effect type, speed, and brightness out-of-the-box.
- **Improved Effect Support**: Unlocked support for `monocolor`, `breathe`, `chroma`, and `rainbow` animations.
- **Refined Speed Calibration**: Maps the 0-100 slider to precise hardware animations (Slow ~13s, Med ~9s, Fast ~5s).
- **Global Brightness Mapping**: Implemented a 4-level intensity map (Off, Low, Med, High) that works even with autonomous animations (Rainbow/Chroma).
- **Haptic Response Curve**: Introduced a non-linear software curve system to preserve subtle haptics while keeping heavy rumble quiet.
- **Full Range Unlocked**: Corrected sysfs limits for both Vibration and Deadzones to allow the full 0-100% range.
- **Pure Software Scaling**: Decoupled hardware intensity to prevent "Double Scaling," preserving high haptic resolution at low power levels.

## Configuration

### Vibration & Haptics
The patched driver includes a sophisticated haptic engine designed to fix the "noisy motors" issue on the Ally X without losing detail.

- `/sys/devices/.../vibration_intensity`: Sets the **Master Cap** (Max power).
- `/sys/devices/.../vibration_floor`: Sets the **Feelable Minimum**. Any vibration from a game will be boosted to at least this level to prevent it being lost.
- `/sys/devices/.../vibration_curve`: Adjusts the **Response Shape**. High values (e.g., 300) boost the low-end heavily, making subtle effects like wind or footsteps feel tactile even at low overall intensities.

> [!NOTE]
> Current defaults (calibrated on the Ally X): Intensity=8, Floor=8, Curve=400.


## Installation (Recommended)

The easiest way to install and maintain the driver is using the included `install.sh` script. This script handles filesystem unlocking, dependencies, building, and reloading automatically.

```bash
cd ally_module
sudo ./install.sh
```

> [!TIP]
> **SteamOS Updates**: When SteamOS performs a system update, your filesystem and drivers are reset. Simply re-run `sudo ./install.sh` to restore the patch for the Ally X LED controls.

---

## Uninstallation

To remove the patch and restore the original system driver:

```bash
cd ally_module
sudo ./uninstall.sh
```

The script will automatically restore the original driver from a backup. If no backup is available, it will offer to reinstall the official kernel package to ensure your system returns to its original state.

---

## Manual Build & Install Instructions (Advanced)

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

# 6. Verify logs (if enabled)
sudo dmesg | grep -i "ally"
```

### Expected Logs
A successful installation will show the following style of output in your logs:

```text
# Initial driver creation and Ally X registration
[18463.905255] asus_rog_ally: Created Ally RGB LED controls.
[18463.907606] asus_rog_ally: LED brightness: level=2
[18463.971627] asus_rog_ally: Registered Ally X controller using input250
[18463.971651] asus_rog_ally: Created Ally X controller.
```

## Credits
Based on the ASUS ROG HID driver by Luke Jones.
