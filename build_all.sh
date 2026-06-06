#!/bin/bash

set -e

if [ "$EUID" -ne 0 ]; then
    echo "Please run this script with sudo: sudo ./build_all.sh"
    exit 1
fi

KVER=$(uname -r)
KDIR="/lib/modules/${KVER}/build"

if [ ! -f "${KDIR}/include/generated/autoconf.h" ]; then
    echo "Missing generated/autoconf.h, reinstalling kernel headers to fix..."
    HEADER_SUFFIX=$(echo "$KVER" | grep -o 'neptune-[0-9]*' || echo "")
    if [ -n "$HEADER_SUFFIX" ]; then
        HEADER_PKG="linux-${HEADER_SUFFIX}-headers"
    else
        HEADER_PKG="linux-headers"
    fi
    pacman -S --noconfirm "$HEADER_PKG"
fi

echo "======================================"
echo "Building ally_module"
echo "======================================"
echo "Cleaning old build files..."
rm -f .*.cmd *.o *.ko *.mod* modules.order Module.symvers
make all

echo ""
echo "======================================"
echo "Building usbhid_custom"
echo "======================================"
cd usbhid_custom
echo "Cleaning old build files..."
rm -f .*.cmd *.o *.ko *.mod* modules.order Module.symvers
make all
cd ..

echo ""
echo "======================================"
echo "All builds completed successfully!"
echo "======================================"
