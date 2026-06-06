#!/bin/bash

# Standalone installer for usbhid_custom

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
    error "Please run this script with sudo: sudo ./install.sh"
fi

if [ -f /usr/bin/steamos-readonly ]; then
    log "Ensuring filesystem is writeable..."
    steamos-readonly disable
fi

KVER=$(uname -r)
KDIR="/lib/modules/${KVER}/build"
INSTALL_PATH="/lib/modules/${KVER}/kernel/drivers/hid/"

if [ ! -d "$KDIR" ] || [ ! -f "${KDIR}/include/generated/autoconf.h" ]; then
    log "Kernel headers or autoconf.h missing. Reinstalling headers..."
    HEADER_SUFFIX=$(echo "$KVER" | grep -o 'neptune-[0-9]*' || echo "")
    if [ -n "$HEADER_SUFFIX" ]; then
        HEADER_PKG="linux-${HEADER_SUFFIX}-headers"
    else
        HEADER_PKG="linux-headers"
    fi
    pacman -S --noconfirm "$HEADER_PKG"
fi

log "Cleaning old build files..."
rm -f .*.cmd *.o *.ko *.mod* modules.order Module.symvers

log "Building usbhid_custom..."
make all KDIR="${KDIR}"

if [ ! -f "usbhid_custom.ko" ]; then
    error "Build failed! usbhid_custom.ko not found."
fi

log "Compressing module..."
zstd -f "usbhid_custom.ko"

mkdir -p "$INSTALL_PATH"
TARGET_FILE="${INSTALL_PATH}/usbhid_custom.ko.zst"

log "Installing to $INSTALL_PATH..."
cp -f "usbhid_custom.ko.zst" "$TARGET_FILE"

log "Updating dependency map..."
depmod -a

log "Installation complete!"
warn "Note: Loading this module manually (modprobe usbhid_custom) may conflict with the built-in usbhid if it handles critical input devices."
