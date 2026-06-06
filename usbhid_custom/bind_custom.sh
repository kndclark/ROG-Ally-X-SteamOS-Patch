#!/bin/bash
# Script to hot-swap USB HID devices from the built-in usbhid to usbhid_custom

if [ "$EUID" -ne 0 ]; then
    echo "Please run this script with sudo: sudo ./bind_custom.sh"
    exit 1
fi

echo "Loading usbhid_custom module..."
modprobe usbhid_custom || { echo "Failed to load usbhid_custom. Did you run install.sh?"; exit 1; }

echo "Scanning for active USB HID devices on built-in driver..."
count=0
for intf in /sys/bus/usb/drivers/usbhid/*:*; do
    if [ -d "$intf" ]; then
        dev_id=$(basename "$intf")
        echo "Hot-swapping device $dev_id..."
        
        # Unbind from default driver
        echo -n "$dev_id" > /sys/bus/usb/drivers/usbhid/unbind 2>/dev/null || true
        
        # Bind to custom driver
        echo -n "$dev_id" > /sys/bus/usb/drivers/usbhid_custom/bind 2>/dev/null || echo "  Warning: Failed to bind $dev_id to usbhid_custom"
        
        echo "  Handed $dev_id over to usbhid_custom."
        count=$((count + 1))
    fi
done

if [ "$count" -eq 0 ]; then
    echo "No devices were found currently bound to the default 'usbhid' driver."
else
    echo "Hot-swap complete! $count device(s) transferred."
fi
