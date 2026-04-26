# ROG Ally X Driver (Unified hid-asus)

This repository contains a specialized version of the `hid-asus` kernel driver, optimized for the ASUS ROG Ally X on SteamOS. It builds upon the upstream work by Denis (NeroReflex) and Luke Jones, adding critical features for handheld usability and SteamOS integration.

## Key Enhancements (Diff from Upstream)

Compared to the base `hid-asus` driver, this version introduces the following functional updates:

### 1. Full Aura RGB LED Support
*   **Integrated Effects**: Implements the `0x5A/0x5D` LED protocol directly into the driver, enabling `Static`, `Breathe`, `Chroma`, and `Rainbow` animations.
*   **Multicolor Class Integration**: Exposes LEDs via the standard `led_classdev_multicolor` interface, allowing native control of intensity and color components.
*   **Persistence Cache**: Added logic to cache unscaled color intensity and global brightness, ensuring LED states survive system-wide dimming events.

### 2. SteamOS GameMode Compatibility
*   **Sysfs Collision Guards**: Includes specific logic to handle the `left_joystick_axis` group collision present on Valve's patched Neptune kernels, ensuring the customization menu remains visible.
*   **Standardized Attributes**: Exposes `rgb_mode`, `rgb_speed`, and `rgb_brightness` as standard attributes on the LED device, matching the paradigm used in mainline handheld drivers.

### 3. Stabilized Power Management
*   **Asynchronous Initialization**: Replaced blocking `msleep` calls with an asynchronous `delayed_work` queue (1.5s window). This prevents the driver from blocking the kernel's resume path while ensuring the MCU is ready before receiving configuration.
*   **Resume State Restoration**: Implemented automatic restoration of LED effects and brightness upon wake-up, bypassing the "resume-to-off" behavior common on handheld MCUs.

### 4. Advanced Input Handling
*   **Double-Mapping Suppression**: Updated `asus_raw_event` to return `-1` for Ally vendor reports. This prevents duplicate input reporting where the system would otherwise map the same button twice (once via the HID core and once via the driver).
*   **Resume Force-Release**: Added a safety mechanism to clear the state of all vendor buttons (Armoury Crate, Command Center) during hardware re-initialization.

## Prerequisites

- **User deck password set**: SteamOS requires a password for `sudo` actions.
- **Persistent storage**: It is recommended to clone this repository to `/home/deck/` to preserve it during system updates.

## Installation

The included `install.sh` script handles filesystem unlocking, out-of-tree building (using `asus-wmi-stub`), and module reloading automatically.

```bash
cd ally_module
sudo ./install.sh
```

> [!TIP]
> **SteamOS Updates**: When SteamOS performs a system update, your filesystem and drivers are reset. Simply re-run `sudo ./install.sh` to restore these enhancements.

## Credits
Based on the ASUS HID driver work by Luke Jones, Denis (NeroReflex), and Derek J. Clark.
Special thanks to the ROG Ally reverse-engineering community.
