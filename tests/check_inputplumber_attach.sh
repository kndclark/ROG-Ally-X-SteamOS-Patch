#!/bin/bash
# check_inputplumber_attach.sh - did InputPlumber accept the driver this time?
#
# READ-ONLY. Reads sysfs and the journal, changes nothing.
#
# InputPlumber 0.78.0 ships a ROG Ally source driver (src/drivers/rog_ally/)
# that configures the controller over sysfs. Strings from the binary:
#
#     Device is not using the asus_rog_ally driver.
#     btn_m1/remap   KB_F15
#     btn_m2/remap   KB_F14
#     gamepad_mode   apply_all
#     Could not set apply_all to 1
#
# It gates on the bound kernel driver being named "asus_rog_ally". Our module
# normally binds as "asus", so the check fails and none of that configuration
# is ever applied - which is why both paddles stay on their stock binding,
# both emit vendor 0xa5, and both register as R4 in Steam.
#
# The observable that settles it needs no button presses and no labels: if
# InputPlumber attaches, it writes KB_F15 into btn_m1/remap by itself. Stock
# is KB_M1, so the value alone tells us whether the daemon ran.
#
# Usage: sudo ./tests/check_inputplumber_attach.sh

echo "=== bound kernel driver ==="
for d in /sys/bus/hid/devices/*1B4C*; do
	drv=$(basename "$(readlink -f "$d/driver")" 2>/dev/null)
	printf '  %s  driver=%s\n' "$(basename "$d")" "${drv:-<none>}"
done

echo
echo "=== paddle bindings (the tell) ==="
for b in btn_m1 btn_m2; do
	f=$(ls /sys/bus/hid/devices/*1B4C*/$b/remap 2>/dev/null | head -1)
	[ -n "$f" ] && printf '  %s/remap = %s\n' "$b" "$(cat "$f")"
done
echo "  stock is KB_M1 / KB_M2"
echo "  InputPlumber-configured is KB_F15 / KB_F14"

echo
echo "=== InputPlumber: driver rejections since boot ==="
n=$(journalctl -b -u inputplumber --no-pager 2>/dev/null | grep -c "asus_rog_ally driver")
echo "  \"not using the asus_rog_ally driver\" x $n"

echo
echo "=== InputPlumber: last 15 lines mentioning the Ally or hidraw ==="
journalctl -b -u inputplumber --no-pager 2>/dev/null \
	| grep -iE "rog ally|hidraw|apply_all|rog_ally" | tail -15 | sed 's/^/  /'

echo
echo "=== joystick devices visible to userspace ==="
awk '/^N: Name=/{n=$0} /^H: Handlers=/{if (n ~ /Ally|N-KEY/ && $0 ~ /js[0-9]/) print "  "n"\n     "$0}' \
	/proc/bus/input/devices

echo
echo "How to read this:"
echo "  rejections = 0 and btn_m1/remap = KB_F15"
echo "      -> CONFIRMED: the driver name was the only thing blocking it."
echo "  rejections = 0 but remap still KB_M1, with an apply_all error above"
echo "      -> name check passed, missing apply_all attribute is the next blocker."
echo "  rejections still climbing"
echo "      -> the name is not what it gates on; hypothesis is wrong."
