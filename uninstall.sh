#!/bin/bash

# ROG Ally X LED Module Uninstaller for SteamOS
# This script reverts the driver patch and restores the original system driver.

set -e

# --- Configuration ---
MODULE_NAME="hid-asus"
MODULE_FILE="${MODULE_NAME}.ko"
MODULE_ZST="${MODULE_FILE}.zst"
STUB_NAME="asus-wmi-stub"
STUB_ZST="${STUB_NAME}.ko.zst"
INSTALL_PATH="/lib/modules/$(uname -r)/kernel/drivers/hid/"
TARGET_FILE="${INSTALL_PATH}/${MODULE_ZST}"
STUB_TARGET="${INSTALL_PATH}/${STUB_ZST}"
BACKUP_FILE="${TARGET_FILE}.bak"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# --- 1. Environment Checks ---

log "Checking environment..."

if [ "$EUID" -ne 0 ]; then
    error "Please run this script with sudo: sudo ./uninstall.sh"
fi

if [ -f /usr/bin/steamos-readonly ]; then
    log "Ensuring filesystem is writeable..."
    steamos-readonly disable
fi

# --- 2. Restoration Logic ---

if [ -f "$BACKUP_FILE" ]; then
    # Turn off LEDs while driver is still active
    log "Turning off LED rings..."
    for led_path in /sys/class/leds/*:rgb:joystick_rings/brightness; do
        if [ -f "$led_path" ]; then
            echo 0 > "$led_path" || warn "Could not turn off LED at $led_path"
        fi
    done

    log "Restoring original driver from backup ($BACKUP_FILE)..."
    cp -f "$BACKUP_FILE" "$TARGET_FILE"
    rm -f "$BACKUP_FILE"
    rm -f "$STUB_TARGET"
    log "Restoration complete."
else
    warn "No local backup found at $BACKUP_FILE."
    
    # Dynamic detection of owner package
    OWNER_PKG=$(pacman -Qo "$TARGET_FILE" 2>/dev/null | awk '{print $(NF-1)}' || echo "")
    
    if [ -n "$OWNER_PKG" ]; then
        log "Detected owner package: $OWNER_PKG"
        DL_SIZE=$(pacman -Si "$OWNER_PKG" | grep "Download Size" | cut -d: -f2 | xargs || echo "unknown size")
        
        warn "The original driver file is missing or has been overwritten."
        warn "To restore the OS to its original state, the parent package ($OWNER_PKG) needs to be reinstalled."
        warn "Estimated download size: $DL_SIZE"
        
        read -p "Would you like to reinstall $OWNER_PKG now? (y/N) " confirm
        if [[ $confirm =~ ^[Yy]$ ]]; then
            log "Reinstalling $OWNER_PKG..."
            pacman -S --noconfirm "$OWNER_PKG"
        else
            log "Skipping package reinstallation. The patched module will be removed, but the original will NOT be restored."
            read -p "Remove the patched module anyway? (y/N) " confirm_remove
            if [[ $confirm_remove =~ ^[Yy]$ ]]; then
                rm -f "$TARGET_FILE"
                rm -f "$STUB_TARGET"
            else
                log "Uninstallation cancelled."
                exit 0
            fi
        fi
    else
        error "Could not determine owner package for $TARGET_FILE and no backup exists."
    fi
fi

# --- 3. System Sync ---

log "Updating dependency map..."
depmod -a

log "Reloading module..."
if lsmod | grep -q "${MODULE_NAME//-/_}"; then
    modprobe -r "${MODULE_NAME//-/_}"
fi
modprobe "${MODULE_NAME//-/_}" || warn "Original module failed to load. It may have been removed."

log "Uninstallation complete!"
warn "Note: If you re-enabled read-only mode manually, remember to lock it if desired: sudo steamos-readonly enable"
