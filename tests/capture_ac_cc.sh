#!/bin/bash
# capture_ac_cc.sh - capture AC/CC short- and long-press events for inputplumber.
#
# Watches every Ally input node at once, because which node a key lands on is
# part of what we are trying to document:
#   ep 0x81 -> keyboard interface  (handle_ally_event reports keycodes here)
#   ep 0x83 -> config interface    (where the 0x5a vendor reports arrive)
#   ep 0x87 -> gamepad             (also declares F16/F17/PROG1)
#
# One capture window per action so the output is already split per action.
#
# Usage: sudo ./tests/capture_ac_cc.sh

set -u
command -v evtest >/dev/null || { echo "evtest not installed" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "run with sudo (event nodes are root-only)" >&2; exit 1; }

# Collect every event node belonging to the Ally, labelled by its IN endpoint.
NODES=""
for i in /sys/bus/usb/devices/1-2:1.*; do
	ep=$(for e in "$i"/ep_*; do
		[ -e "$e/direction" ] || continue
		[ "$(cat "$e/direction")" = in ] && cat "$e/bEndpointAddress"
	done | head -1)
	for ev in "$i"/0003:0B05:1B4C.*/input/input*/event*; do
		[ -e "$ev" ] || continue
		NODES="$NODES $(basename "$ev"):${ep:-??}"
	done
done
[ -n "$NODES" ] || { echo "no Ally event nodes found" >&2; exit 1; }

echo "watching:"
for n in $NODES; do echo "   /dev/input/${n%%:*}  (ep 0x${n##*:})"; done
echo

OUT=$(mktemp -d)

# InputPlumber EVIOCGRABs the physical nodes and re-emits on a virtual device,
# so nothing reaches evtest while it runs. Stop it for the capture and always
# put it back, including on Ctrl-C.
IP_WAS_ACTIVE=no
if systemctl is-active --quiet inputplumber 2>/dev/null; then
	IP_WAS_ACTIVE=yes
	echo "stopping inputplumber for the capture (will restart afterwards)"
	systemctl stop inputplumber
	sleep 1
fi
cleanup() {
	rm -rf "$OUT"
	if [ "$IP_WAS_ACTIVE" = yes ]; then
		systemctl start inputplumber && echo "inputplumber restarted"
	fi
}
trap cleanup EXIT INT TERM

capture() { # $1 label, $2 seconds, $3 instruction
	echo "──── $1 ────"
	echo "  $3"
	printf '  Enter when ready... '; read -r _
	echo "  capturing ${2}s..."
	for n in $NODES; do
		timeout "$2" evtest "/dev/input/${n%%:*}" > "$OUT/${n%%:*}.log" 2>/dev/null &
	done
	wait
	local any=0
	for n in $NODES; do
		node=${n%%:*}
		# Only real event lines start with "Event: time"; the capability
		# dump also contains "type 1 (EV_KEY)" and must not be matched.
		lines=$(grep '^Event: time' "$OUT/$node.log" 2>/dev/null | grep 'EV_KEY')
		if [ -n "$lines" ]; then
			any=1
			echo "  [$node ep 0x${n##*:}]"
			echo "$lines" | sed -E 's/^Event: time [0-9.]+, //; s/^/    /'
		fi
	done
	[ "$any" -eq 1 ] || echo "  (no EV_KEY events seen on any node)"
	echo
}

echo "Perform ONLY the named action inside each window."
echo
capture "AC short press" 5 "tap AC (Armoury Crate) once"
capture "AC long press"  8 "press and HOLD AC ~3s, then release"
capture "CC short press" 5 "tap CC (Command Center) once"
capture "CC long press"  8 "press and HOLD CC ~3s, then release"

echo "done - paste the blocks above."
