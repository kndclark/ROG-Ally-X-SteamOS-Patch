#!/bin/bash
# Step 2: find the exact command that hard-resets the Ally on kernel 7.2.
#
# install.sh's reload block dies somewhere between unloading the stock drivers
# and probing ours, and the reset is hard enough that journald loses the last
# ~10s and ramoops captures nothing. So instead of relying on logs, this script
# writes a synced marker to disk BEFORE each command and another after it. If
# the machine dies, the last "BEGIN" with no matching "END" names the culprit.
#
# It also splits module load from device probe, which install.sh cannot do:
# with /sys/bus/hid/drivers_autoprobe set to 0 the module can be inserted
# without binding anything, then interfaces are bound one at a time. A crash on
# insmod means module init; a crash on a bind names the interface whose probe
# path faults.
#
# EXPECT THIS TO HARD-RESET THE MACHINE. That is the point - the marker file is
# what survives. Run it over SSH from another machine; the Ally's built-in
# keyboard and gamepad go dead partway through by design.
#
# Usage:
#   sudo ./tests/crash_isolate.sh              # unbind interfaces, then unload
#   sudo ./tests/crash_isolate.sh --no-unbind  # faithful repro of install.sh
#   sudo ./tests/crash_isolate.sh --quiesce    # stop InputPlumber first
#   sudo ./tests/crash_isolate.sh --report     # just read the log from last run
#
# The 2026-08-28 run established that `modprobe -r hid_asus_ally` is what
# resets the box, before our module is ever inserted. That leaves one question
# the fix depends on: is the fault in the stock driver's per-device remove()
# path, or in its module teardown? Unbinding an interface calls remove()
# WITHOUT tearing the module down, so unbinding first separates the two. If the
# unbinds survive and the later rmmod still dies, the fault is in teardown and
# install.sh can unbind before unloading. If an unbind itself dies, remove() is
# fatal and install.sh must not touch the stock driver at all.
#
# --quiesce is for a follow-up run: if the crash needs InputPlumber to be
# holding the device open, stopping it first will make it go away.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${REPO_DIR}/tests/crash_isolate.log"
MODULE="${REPO_DIR}/hid-asus.ko"
AUTOPROBE=/sys/bus/hid/drivers_autoprobe

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
say()   { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
die()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

QUIESCE=false
REPORT_ONLY=false
UNBIND_FIRST=true
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --quiesce)   QUIESCE=true ;;
        --report)    REPORT_ONLY=true ;;
        --no-unbind) UNBIND_FIRST=false ;;
        *) die "Unknown parameter: $1" ;;
    esac
    shift
done

# --- Report mode: read the previous run's markers -----------------------------
if [ "$REPORT_ONLY" = true ]; then
    [ -f "$LOG" ] || die "No log at $LOG - nothing to report."
    echo "=== $LOG ==="
    cat "$LOG"
    echo
    echo "=== VERDICT ==="
    # "RUN COMPLETE" is written only after the last step returns, so its
    # presence settles the question outright. Check it FIRST: an earlier
    # version compared the BEGIN and END labels without stripping END's
    # "(exit N)" suffix, so they never matched and every run - crashed or not -
    # was reported as a crash.
    if grep -q '^RUN COMPLETE' "$LOG"; then
        echo "NO CRASH. The run reached the end and wrote RUN COMPLETE."
        FAILED=$(grep '^END .*(exit [^0]' "$LOG" | sed 's/^END /  /')
        if [ -n "$FAILED" ]; then
            echo "Steps that ran but returned non-zero:"
            echo "$FAILED"
        else
            echo "Every step returned 0."
        fi
    else
        # No completion line: the run died. An unmatched BEGIN names the
        # command that was in flight. Strip END's exit suffix before comparing.
        LAST_BEGIN=$(grep '^BEGIN ' "$LOG" | tail -1 | sed 's/^BEGIN //')
        LAST_END=$(grep '^END ' "$LOG" | tail -1 | sed 's/^END //; s/ (exit -\?[0-9]\+)$//')
        if [ "$LAST_BEGIN" = "$LAST_END" ]; then
            echo "Interrupted BETWEEN steps, not during one."
            echo "Last step that completed: ${LAST_END:-<none>}"
        else
            echo "CRASHED DURING: $LAST_BEGIN"
            echo "Last step that completed: ${LAST_END:-<none>}"
        fi
    fi
    echo
    echo "=== pstore (kernel dump from the crash, if the reset was warm) ==="
    if [ -n "$(ls -A /sys/fs/pstore 2>/dev/null)" ]; then
        ls -la /sys/fs/pstore
        echo "--- contents ---"
        for f in /sys/fs/pstore/*; do echo "### $f"; cat "$f"; done
    else
        echo "empty - the reset was a power cycle (DRAM lost), or no oops was raised."
    fi
    exit 0
fi

[ "$EUID" -eq 0 ] || die "Run with sudo: sudo ./tests/crash_isolate.sh"
[ -f "$MODULE" ] || die "No module at $MODULE - run 'make all' in $REPO_DIR first."

MOD_VERMAGIC=$(modinfo "$MODULE" | awk '/^vermagic:/{print $2}')
[ "$MOD_VERMAGIC" = "$(uname -r)" ] \
    || die "Module vermagic '$MOD_VERMAGIC' != running kernel '$(uname -r)'. Rebuild first."
[ -w "$AUTOPROBE" ] || die "$AUTOPROBE not writable - cannot suppress auto-binding."

# --- Marker plumbing ----------------------------------------------------------
# Every marker is fsync'd to disk and echoed to /dev/kmsg. The disk copy is the
# one that matters (it survives a power cycle); the kmsg copy lands in the
# ramoops console buffer and survives a warm reset, so between the two we get a
# record either way.
DMESG_POS=0
mark() {
    printf '%s\n' "$*" >> "$LOG"
    sync
    echo "crash_isolate: $*" > /dev/kmsg 2>/dev/null || true
}
note() { printf '    %s\n' "$*" >> "$LOG"; sync; }

# driver_of <sysfs device dir> -> the bound driver's name, or NONE.
# Test the symlink explicitly: `readlink -f` on a MISSING driver link happily
# returns the path it was given, so basename yields the literal string
# "driver". An earlier version did exactly that, reported unbound devices as
# "bound to driver", and skipped the whole bind phase as a result.
driver_of() {
    [ -L "$1/driver" ] || { echo NONE; return; }
    basename "$(readlink -f "$1/driver")"
}

# Unbind every ROG Ally interface from whatever driver currently holds it.
# Needed repeatedly: removing a HID driver releases its devices, and the
# remaining drivers (hid-generic, then stock hid_asus) immediately re-match
# them. A manual sysfs unbind does not re-probe, so it sticks.
unbind_all_ally() {
    local d dev drv
    for d in /sys/bus/hid/devices/0003:0B05:1B4C.*; do
        [ -e "$d" ] || continue
        dev=$(basename "$d"); drv=$(driver_of "$d")
        [ "$drv" = "NONE" ] && continue
        echo "$dev" > "/sys/bus/hid/drivers/$drv/unbind" 2>/dev/null \
            && note "unbound $dev from $drv" \
            || note "FAILED to unbind $dev from $drv"
    done
    sync
}

capture_dmesg() {
    local total new
    total=$(dmesg | wc -l)
    if [ "$total" -gt "$DMESG_POS" ]; then
        new=$(dmesg | tail -n +$((DMESG_POS + 1)))
        printf '    --- dmesg ---\n' >> "$LOG"
        printf '%s\n' "$new" | sed 's/^/    /' >> "$LOG"
        DMESG_POS=$total
        sync
    fi
}

# step <label> <command...>
# Writes BEGIN, runs the command, writes END with its exit status. A hard reset
# between the two leaves BEGIN unmatched, which is the whole point.
step() {
    local label="$1"; shift
    mark "BEGIN $label"
    note "cmd: $*"
    local out rc
    out=$("$@" 2>&1); rc=$?
    [ -n "$out" ] && note "output: $out"
    note "exit: $rc"
    capture_dmesg
    mark "END $label (exit $rc)"
    sleep 2          # let a deferred fault land while this step still owns the marker
    capture_dmesg
    return $rc
}

# --- Start a fresh log --------------------------------------------------------
: > "$LOG"
mark "RUN START $(date -Is)"
note "kernel: $(uname -r)"
note "module: $MODULE (vermagic $MOD_VERMAGIC)"
note "quiesce: $QUIESCE"
note "unbind_first: $UNBIND_FIRST"
DMESG_POS=$(dmesg | wc -l)

say "Logging to $LOG"
say "Kernel $(uname -r)"

# Carry forward anything the PREVIOUS crash left in pstore before it is cleared.
if [ -n "$(ls -A /sys/fs/pstore 2>/dev/null)" ]; then
    mark "PSTORE FROM PREVIOUS CRASH"
    for f in /sys/fs/pstore/*; do
        note "### $f"
        sed 's/^/    /' "$f" >> "$LOG"
    done
    sync
    warn "pstore held a dump from a previous crash - copied into the log."
fi

mark "PREFLIGHT"
note "lsmod: $(lsmod | grep -E 'hid_asus|asus_wmi|led_class_multicolor|ff_memless' | tr '\n' ';')"
for d in /sys/bus/hid/devices/0003:0B05:1B4C.*; do
    [ -e "$d" ] || continue
    note "$(basename "$d") -> $(driver_of "$d")"
done

# An empty /sys/fs/pstore after a crash only means "the reset was a power cycle"
# if ramoops was configured to dump oopses in the first place. These parameters
# are root-only, so record them here rather than guessing from an empty pstore.
note "--- ramoops parameters ---"
for p in /sys/module/ramoops/parameters/*; do
    [ -r "$p" ] || continue
    note "$(basename "$p") = $(cat "$p" 2>/dev/null)"
done

echo
warn "The machine is expected to hard-reset during this run."
warn "After it comes back, run:  sudo ./tests/crash_isolate.sh --report"
echo
say "Starting in 5 seconds - Ctrl-C to abort."
sleep 5

# --- Optional: get userspace off the device -----------------------------------
if [ "$QUIESCE" = true ]; then
    step "quiesce-inputplumber" systemctl stop inputplumber
fi

# --- Keep udev from undoing the swap underneath us ----------------------------
# Last run, stock hid_asus was reloaded seconds after being removed and had
# re-bound four interfaces by the time we tried to insert ours - which is why
# insmod failed with "File exists". udev does that: an unbound HID device
# matches the stock module's modalias and udev loads it straight back. Our
# module is ALSO named hid_asus, so a modprobe blacklist cannot separate the
# two. Pausing udev's event queue for the duration is the only clean way to
# hold the window open.
step "udev-pause" udevadm control --stop-exec-queue

# --- Suppress auto-binding on driver registration -----------------------------
# This stops a NEWLY REGISTERED driver from probing devices, which is what lets
# insmod be timed separately from the first bind. It does NOT stop devices from
# being re-matched when a driver is removed - last run hid-generic grabbed all
# six interfaces the moment ally went away, with autoprobe already 0. That is
# what unbind_all_ally is for.
step "autoprobe-off" bash -c "echo 0 > $AUTOPROBE"

# --- Unbind the stock driver's interfaces, one at a time ---------------------
# Confirmed on 2026-08-28: the rmmod below is what resets the box. Unbinding
# runs the same driver's remove() against one device at a time but leaves the
# module loaded, so whichever of these two steps dies tells us which half of
# the teardown is fatal - and therefore whether install.sh can unbind its way
# out of the problem or has to stop touching the stock driver entirely.
if [ "$UNBIND_FIRST" = true ]; then
    for d in /sys/bus/hid/devices/0003:0B05:1B4C.*; do
        [ -e "$d" ] || continue
        dev=$(basename "$d")
        drv=$(driver_of "$d")
        if [ "$drv" != "asus_rog_ally" ]; then
            mark "SKIP unbind-$dev (driver is $drv, not asus_rog_ally)"
            continue
        fi
        step "unbind-$dev" bash -c "echo '$dev' > /sys/bus/hid/drivers/asus_rog_ally/unbind"
    done
else
    mark "SKIP unbind phase (--no-unbind)"
fi

# --- Unload the stock drivers, one at a time ---------------------------------
# On 6.18.45 hid_asus_ally could not even load ("Unknown symbol
# validate_mcu_fw_version"), so install.sh's modprobe -r was a silent no-op.
# On 7.2 it loads, binds, and InputPlumber attaches to it - so this teardown is
# doing real work for the first time, on the exact kernel where the box started
# resetting. It is the confirmed point of failure.
#
# rmmod, not `modprobe -r`. The 2026-08-28 run showed `modprobe -r
# hid_asus_ally` also removes hid_asus, because ally depends on it and modprobe
# cleans up dependencies that fall to zero users: right after that command,
# hid-generic re-bound all six interfaces, which only happens if stock hid_asus
# went away too. That matters twice over. It means the crashing command tore
# down BOTH modules, so which one is actually fatal is still open; and it means
# the two teardowns have to be issued separately to tell them apart at all.
if lsmod | grep -q '^hid_asus_ally'; then
    step "rmmod-hid_asus_ally" rmmod hid_asus_ally
else
    mark "SKIP rmmod-hid_asus_ally (not loaded)"
fi

# Releasing ally's devices lets the remaining drivers re-match them, so clear
# them again before touching hid_asus - otherwise its module refcount is
# non-zero and the rmmod fails with "Module hid_asus is in use", which is
# exactly what happened last run.
mark "UNBIND-ALL before rmmod-hid_asus"
unbind_all_ally

if lsmod | grep -q '^hid_asus\b'; then
    step "rmmod-hid_asus" rmmod hid_asus
else
    mark "SKIP rmmod-hid_asus (not loaded)"
fi

# --- Preload our module's dependencies ---------------------------------------
# insmod does not resolve dependencies, which is deliberate here: it lets us
# load our module WITHOUT asus-wmi-stub. On 7.2 the stub is a duplicate-symbol
# conflict (asus_wmi exports asus_hid_register_listener, _unregister_listener
# and asus_hid_event itself), so this also confirms the module loads clean once
# the stub is out of the picture.
step "modprobe-deps" modprobe led-class-multicolor ff-memless asus-wmi

# --- Load our module, without binding anything -------------------------------
step "insmod-hid_asus" insmod "$MODULE"

# Find the driver directory our module registered under.
DRVDIR=""
for d in /sys/bus/hid/drivers/*; do
    [ -e "$d/module" ] || continue
    if [ "$(basename "$(readlink -f "$d/module")")" = "hid_asus" ]; then
        DRVDIR="$d"; break
    fi
done
if [ -z "$DRVDIR" ]; then
    mark "ABORT no hid driver owned by hid_asus appeared after insmod"
    warn "Module loaded but registered no HID driver - stopping here."
    echo 1 > "$AUTOPROBE"
    udevadm control --start-exec-queue
    warn "udev resumed; reboot to get back to a known-good binding."
    exit 1
fi
mark "DRIVER $DRVDIR"

# --- Bind one interface at a time --------------------------------------------
# This is what install.sh can never show: which interface's probe faults. The
# .0003 and .0006 interfaces are the Ally config and gamepad ones, where
# QUIRK_ROG_ALLY_XPAD sends us through validate_mcu_fw_version() and into
# set_ally_mcu_hack() / set_ally_mcu_powersave() - WMI writes that reach the EC.
mark "UNBIND-ALL before bind phase"
unbind_all_ally

for d in /sys/bus/hid/devices/0003:0B05:1B4C.*; do
    [ -e "$d" ] || continue
    dev=$(basename "$d")
    cur=$(driver_of "$d")
    if [ "$cur" != "NONE" ]; then
        mark "SKIP bind-$dev (already bound to $cur)"
        continue
    fi
    step "bind-$dev" bash -c "echo '$dev' > '$DRVDIR/bind'"
done

# --- Restore normal behaviour -------------------------------------------------
step "autoprobe-on" bash -c "echo 1 > $AUTOPROBE"
step "udev-resume" udevadm control --start-exec-queue

mark "POSTFLIGHT"
for d in /sys/bus/hid/devices/0003:0B05:1B4C.*; do
    [ -e "$d" ] || continue
    note "$(basename "$d") -> $(driver_of "$d")"
done
note "lsmod: $(lsmod | grep -E 'hid_asus|asus_wmi' | tr '\n' ';')"

mark "RUN COMPLETE $(date -Is)"
echo
say "Run completed WITHOUT a crash. Full log:"
say "    sudo ./tests/crash_isolate.sh --report"
