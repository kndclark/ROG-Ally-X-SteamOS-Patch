#!/bin/bash

# Standalone uninstaller for usbhid_custom

set -e

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

if [ "$EUID" -ne 0 ]; then
    error "Please run this script with sudo: sudo ./uninstall.sh"
fi

if [ -f /usr/bin/steamos-readonly ]; then
    log "Ensuring filesystem is writeable..."
    steamos-readonly disable
fi

INSTALL_PATH="/lib/modules/$(uname -r)/kernel/drivers/hid/"
TARGET_FILE="${INSTALL_PATH}/usbhid_custom.ko.zst"

if [ -f "$TARGET_FILE" ]; then
    log "Removing $TARGET_FILE..."
    rm -f "$TARGET_FILE"
    
    log "Updating dependency map..."
    depmod -a
    
    log "Cleaning local build files..."
    rm -f .*.cmd *.o *.ko *.mod* modules.order Module.symvers
    
    log "Uninstallation complete!"
else
    warn "Module file not found at $TARGET_FILE. Nothing to uninstall."
fi
