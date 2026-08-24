#!/bin/bash
# run_reverify_suite.sh - re-confirm the three results most exposed to today's
# driver-binding confusion, against the build that is ACTUALLY loaded now.
#
# Background: a SteamOS update on the `main` branch wiped
# /etc/modprobe.d/hid-asus-ally-blacklist.conf (confirmed via journalctl -
# rauc-override.sh / holo-post-update-shutdown both log the /etc reset), which
# let the stock in-kernel hid_asus_ally.ko silently reclaim the config and
# gamepad interfaces while our custom module stayed loaded elsewhere. Several
# tests ran during that window without anyone knowing which driver they were
# actually talking to. install.sh now verifies interface binding on every run
# and has a --check mode; this script re-runs the tests most likely to have
# been affected, in priority order:
#
#   0. Binding check   - refuse to continue if any interface is still on the
#                         stock driver. Re-running tests against the wrong
#                         driver is exactly the mistake that started this.
#   1. Mode matrix      - btn_a remap across all 4 xbox_controller/gamepad_mode
#                         combinations. Feeds directly into the report to Nero.
#   2. PAD_B remap       - the specific test he asked for. Same reason.
#   3. Haptics smoke test - nothing in the FF code changed today, but the
#                         loaded .ko has been rebuilt several times this
#                         session and was never re-verified after. Automated:
#                         no button presses needed, just watches the wire.
#
# M1/M2 paddle slot order is deliberately NOT re-run here: paddle_position.py
# already proved itself against our module specifically, because its result
# tracked source edits in both directions (swap in -> flipped; revert ->
# flipped back) - only possible if our code was bound both times. Re-run
# paddle_position.py yourself if you want a third confirmation, but it is not
# resolving any actual doubt.
#
# Steps 1 and 2 are interactive: you will be prompted to press physical
# buttons during timed capture windows, same as running them directly. This
# script only saves you from juggling three separate invocations and gives
# one combined summary at the end.
#
# I have not been able to run this myself - no interactive sudo in my shell.
# Treat this first run as validating the orchestrator too, not just the
# driver.
#
# Usage: sudo ./tests/run_reverify_suite.sh

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLY_DIR="$(dirname "$SCRIPT_DIR")"
# Under sudo, $HOME is /root - resolve the invoking user's home instead, or
# ff_sniff.sh is silently "not found" and the haptics step skips itself.
TARGET_USER="${SUDO_USER:-$(whoami)}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
FF_SNIFF=""
for candidate in "$TARGET_HOME/ff_sniff.sh" "$(dirname "$SCRIPT_DIR")/../ff_sniff.sh" /home/deck/ff_sniff.sh; do
    if [ -x "$candidate" ]; then
        FF_SNIFF="$candidate"
        break
    fi
done
HAPTICS_LOG="$SCRIPT_DIR/reverify_haptics.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
section() { echo -e "\n${GREEN}==>${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
    echo "Run with sudo: sudo ./tests/run_reverify_suite.sh" >&2
    exit 1
fi

declare -A RESULT

# --- 0. Binding check -------------------------------------------------
section "0/3  Driver binding check"
if "$ALLY_DIR/install.sh" --check; then
    RESULT[binding]="ok"
else
    fail "One or more interfaces are on the stock driver."
    echo "Run 'sudo $ALLY_DIR/install.sh' to fix this before continuing - anything"
    echo "run past this point would repeat today's actual mistake."
    exit 1
fi

# --- 1. Mode matrix ------------------------------------------------------
section "1/3  Mode matrix (btn_a remap across xbox/gamepad mode combinations)"
echo "Interactive: you'll be asked to press A several times, in desktop mode"
echo "partway through. Make sure no text field is focused."
read -r -p "Press Enter to start, or 's' to skip... " ans
if [ "$ans" = "s" ]; then
    RESULT[mode_matrix]="skipped"
else
    python3 "$SCRIPT_DIR/remap_mode_matrix.py"
    RESULT[mode_matrix]=$?
fi

# --- 2. PAD_B remap -------------------------------------------------------
section "2/3  PAD_B remap (Nero's original test)"
echo "Interactive: stops InputPlumber for the duration, restarts it after."
read -r -p "Press Enter to start, or 's' to skip... " ans
if [ "$ans" = "s" ]; then
    RESULT[pad_b]="skipped"
else
    python3 "$SCRIPT_DIR/pad_remap_test.py"
    RESULT[pad_b]=$?
fi

# --- 3. Haptics smoke test -------------------------------------------------
section "3/3  Haptics smoke test (automated, no button presses)"
if [ -z "$FF_SNIFF" ]; then
    warn "ff_sniff.sh not found (looked in $TARGET_HOME and /home/deck) - skipping."
    warn "Run it manually alongside fftest if you want haptics checked."
    RESULT[haptics]="skipped"
else
    NODE=$(awk '/ASUS ROG Ally X Gamepad/{g=1} g&&/Handlers/{match($0,/event[0-9]+/);print substr($0,RSTART,RLENGTH);exit}' /proc/bus/input/devices)
    if [ -z "$NODE" ]; then
        warn "Could not find the Ally gamepad evdev node - skipping."
        RESULT[haptics]="skipped"
    elif ! command -v fftest >/dev/null; then
        warn "fftest not installed - skipping."
        RESULT[haptics]="skipped"
    else
        echo "Using /dev/input/$NODE"
        : > "$HAPTICS_LOG"
        "$FF_SNIFF" "$HAPTICS_LOG" >/dev/null 2>&1 &
        sleep 1.5   # let it attach to usbmon before fftest fires anything

        # Scripted fftest session: upload happens automatically on start, then
        # Strong Rumble (4), hold, Weak Rumble (5), hold, exit. The sleeps keep
        # the pipe (and therefore the device fd) open - fftest exits on EOF and
        # closing the fd stops the effect, so the sleep length IS the rumble
        # length. Each effect is re-triggered once because fftest's rumble
        # effects run ~5s, so a single trigger would fade before the window ends.
        { echo 4; sleep 4; echo 4; sleep 4; echo 5; sleep 4; echo 5; sleep 4; echo -1; } \
            | fftest "/dev/input/$NODE" >/dev/null

        sleep 1
        pkill -f "stdbuf -oL cat /sys/kernel/debug/usb/usbmon" 2>/dev/null

        lines=$(grep -c "strong=" "$HAPTICS_LOG" 2>/dev/null || echo 0)
        if [ "$lines" -eq 0 ]; then
            fail "No FF reports captured at all - this is the same 'silent channel'"
            echo "        failure mode that cost us hours on the gamepad interface earlier."
            echo "        Check: is usbmon loaded, is this the right bus, is fftest actually"
            echo "        talking to $NODE?"
            RESULT[haptics]="fail: no reports captured"
        else
            overshoot=$(grep -oE 'strong=[[:space:]]*[0-9]+|weak=[[:space:]]*[0-9]+' "$HAPTICS_LOG" \
                | grep -oE '[0-9]+' \
                | awk '$1>100{c++} END{print c+0}')
            echo "  $lines report(s) captured, log: $HAPTICS_LOG"
            if [ "${overshoot:-0}" -gt 0 ]; then
                fail "$overshoot value(s) exceeded 100 - this is the exact >>9 scaling regression"
                echo "        that was fixed 2026-06-19. Check $HAPTICS_LOG."
                RESULT[haptics]="fail: overshoot"
            else
                echo "  All captured values stayed within 0-100. Sample:"
                tail -6 "$HAPTICS_LOG" | sed 's/^/    /'
                RESULT[haptics]="ok"
            fi
        fi
    fi
fi

# --- Summary ---------------------------------------------------------------
section "Summary"
for k in binding mode_matrix pad_b haptics; do
    v="${RESULT[$k]:-not run}"
    printf "  %-14s %s\n" "$k" "$v"
done
echo
echo "mode_matrix / pad_b: read their own output above for the actual per-case"
echo "results (this summary only shows whether the script ran/exited cleanly)."
if [ "${RESULT[haptics]}" != "skipped" ]; then
    echo "haptics: $HAPTICS_LOG has the raw wire capture if you want to look yourself."
fi
