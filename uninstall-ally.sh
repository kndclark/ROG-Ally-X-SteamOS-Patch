#!/bin/bash
set -e

MODULE_NAME="hid-asus-ally"
INSTALL_PATH="/lib/modules/$(uname -r)/kernel/drivers/hid/"

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo."
    exit 1
fi

if [ -f /usr/bin/steamos-readonly ]; then
    steamos-readonly disable
fi

echo "Removing $MODULE_NAME..."
rm -f "${INSTALL_PATH}/${MODULE_NAME}.ko.zst"
depmod -a
modprobe -r "$MODULE_NAME" || true

# Restore hid-asus if backup exists
ASUS_BACKUP="${INSTALL_PATH}/hid-asus.ko.zst.bak"
ASUS_TARGET="${INSTALL_PATH}/hid-asus.ko.zst"
if [ -f "$ASUS_BACKUP" ]; then
    echo "Restoring original hid-asus from backup..."
    cp -f "$ASUS_BACKUP" "$ASUS_TARGET"
    depmod -a
    modprobe -r hid-asus || true
    modprobe hid-asus
fi

echo "Uninstallation complete. System restored to upstream."
