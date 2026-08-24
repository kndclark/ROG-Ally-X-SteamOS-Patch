#!/bin/bash
# test_remap_layout.sh - isolate the button-remap value-byte offset per code type.
#
# The driver writes the remap value at a type-dependent offset inside the
# 11-byte mapping entry:
#   PAD -> btn_bytes[1], KB -> [2], MEDIA -> [3], MOUSE -> [4]
# Nero reports MEDIA (vol+/-) remapping works but PAD_A does not, which points
# at one of those offsets being wrong.
#
# For each code type this remaps one button, then CAPTURES what the button
# actually emits, so the result is measured rather than eyeballed.
#
# The original mapping is restored on exit, and InputPlumber (which grabs the
# physical nodes) is stopped for the duration and restarted afterwards.
#
# Usage: sudo ./tests/test_remap_layout.sh [btn_m1|btn_m2]

set -u
BTN="${1:-btn_m1}"
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
command -v evtest >/dev/null || { echo "evtest not installed" >&2; exit 1; }

# /sys/bus/hid/devices/* are symlinks, so glob instead of find (which will not
# traverse them without -L).
SYS=$(ls -d /sys/bus/hid/devices/*/"$BTN" 2>/dev/null | head -1)
[ -n "$SYS" ] || { echo "Could not find $BTN - is the driver loaded?" >&2; exit 1; }
[ -w "$SYS/remap" ] || { echo "$SYS/remap not writable" >&2; exit 1; }

# Every Ally event node, so we see the emitted event wherever it lands.
NODES=""
for i in /sys/bus/usb/devices/1-2:1.*; do
	for ev in "$i"/0003:0B05:1B4C.*/input/input*/event*; do
		[ -e "$ev" ] && NODES="$NODES $(basename "$ev")"
	done
done

ORIG=$(cat "$SYS/remap" 2>/dev/null)
OUT=$(mktemp -d)
IP_WAS_ACTIVE=no
if systemctl is-active --quiet inputplumber 2>/dev/null; then
	IP_WAS_ACTIVE=yes
	systemctl stop inputplumber
	sleep 1
fi
cleanup() {
	[ -n "${ORIG:-}" ] && echo "$ORIG" > "$SYS/remap" 2>/dev/null && \
		echo "restored $BTN to $ORIG"
	rm -rf "$OUT"
	[ "$IP_WAS_ACTIVE" = yes ] && systemctl start inputplumber && \
		echo "inputplumber restarted"
}
trap cleanup EXIT INT TERM

echo "button   : $SYS"
echo "original : $ORIG"
echo "nodes    :$NODES"
echo

for CODE in MEDIA_VOL_UP PAD_A KB_F8 MOUSE_LCLICK; do
	echo "──── $CODE ────"
	if ! echo "$CODE" > "$SYS/remap" 2>/dev/null; then
		echo "  REJECTED by driver (code not recognised)"
		echo
		continue
	fi
	echo "  remap reads back: $(cat "$SYS/remap" 2>/dev/null)"
	printf '  press %s a few times during the next 5s. Enter to start... ' "$BTN"
	read -r _
	for n in $NODES; do
		timeout 5 evtest "/dev/input/$n" > "$OUT/$n.log" 2>/dev/null &
	done
	wait
	any=0
	for n in $NODES; do
		lines=$(grep '^Event: time' "$OUT/$n.log" 2>/dev/null | grep -E 'EV_KEY|EV_REL|EV_ABS')
		if [ -n "$lines" ]; then
			any=1
			echo "  [$n]"
			echo "$lines" | sed -E 's/^Event: time [0-9.]+, //; s/^/    /'
		fi
	done
	[ "$any" -eq 1 ] || echo "  NO EVENTS - this code type does not reach userspace"
	echo
done

echo "Types that produced events worked; types with NO EVENTS point at a wrong"
echo "value-byte offset for that type."
