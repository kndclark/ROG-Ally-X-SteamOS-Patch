#!/bin/bash
set -e

MODULE_NAME="hid-asus"
INSTALL_PATH="/lib/modules/$(uname -r)/kernel/drivers/hid/"
BACKUP_FILE="${INSTALL_PATH}/${MODULE_NAME}.ko.zst.bak"
TARGET_FILE="${INSTALL_PATH}/${MODULE_NAME}.ko.zst"

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo."
    exit 1
fi

if [ -f /usr/bin/steamos-readonly ]; then
    steamos-readonly disable
fi

if [ -f "$BACKUP_FILE" ]; then
    echo "Restoring original $MODULE_NAME from backup..."
    cp -f "$BACKUP_FILE" "$TARGET_FILE"
    depmod -a
    modprobe -r "$MODULE_NAME" || true
    modprobe "$MODULE_NAME"
    echo "Restoration complete."
else
    echo "Warning: Backup file $BACKUP_FILE not found. If you already restored it, you can ignore this."
fi

# Also ensure hid-asus-ally is removed just in case
if [ -f "${INSTALL_PATH}/hid-asus-ally.ko.zst" ]; then
    echo "Removing hid-asus-ally..."
    rm -f "${INSTALL_PATH}/hid-asus-ally.ko.zst"
    depmod -a
    modprobe -r hid-asus-ally || true
fi
