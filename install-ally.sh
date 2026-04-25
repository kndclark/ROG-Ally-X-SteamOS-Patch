#!/bin/bash
set -e

MODULE_NAME="hid-asus-ally"
INSTALL_PATH="/lib/modules/$(uname -r)/kernel/drivers/hid/"
# Get the actual user if running via sudo
TARGET_USER="${SUDO_USER:-$(whoami)}"

log() { echo -e "\033[0;32m[INFO]\033[0m $1"; }
error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; exit 1; }

if [ "$EUID" -ne 0 ]; then
    error "Please run with sudo."
fi

if [ -f /usr/bin/steamos-readonly ]; then
    log "Ensuring filesystem is writeable..."
    steamos-readonly disable
fi

log "Building $MODULE_NAME..."
make clean
make hid-asus-ally.ko

if [ ! -f "${MODULE_NAME}.ko" ]; then
    error "Build failed!"
fi

log "Compressing module..."
zstd -f "${MODULE_NAME}.ko"

log "Checking for conflicting hid-asus changes..."
# If hid-asus was replaced by our unified driver, we should ideally restore it
# or at least warn the user.
ASUS_BACKUP="${INSTALL_PATH}/hid-asus.ko.zst.bak"
if [ -f "$ASUS_BACKUP" ]; then
    log "Restoring original hid-asus to avoid conflicts..."
    cp -f "$ASUS_BACKUP" "${INSTALL_PATH}/hid-asus.ko.zst"
fi

log "Installing $MODULE_NAME to $INSTALL_PATH..."
cp -f "${MODULE_NAME}.ko.zst" "$INSTALL_PATH"

log "Updating dependency map..."
depmod -a

log "Reloading modules..."
modprobe -r hid-asus || true
modprobe -r hid-asus-ally || true
modprobe hid-asus
modprobe hid-asus-ally

log "Verifying installation..."
if lsmod | grep -q "hid_asus_ally"; then
    log "Module '$MODULE_NAME' loaded successfully."
else
    error "Module failed to load."
fi

log "Installation complete!"
