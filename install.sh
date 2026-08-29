#!/bin/bash

# ROG Ally X LED Module Installer for SteamOS
# This script automates building and installing the hid-asus module.

set -e

# --- Configuration ---
MODULE_NAME="hid-asus"
MODULE_FILE="${MODULE_NAME}.ko"
MODULE_ZST="${MODULE_FILE}.zst"
STUB_NAME="asus-wmi-stub"
STUB_FILE="${STUB_NAME}.ko"
STUB_ZST="${STUB_FILE}.zst"
# INSTALL_PATH will be set after kernel version detection
# Get the actual user if running via sudo
TARGET_USER="${SUDO_USER:-$(whoami)}"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# --- Arguments ---
FORCE_BACKUP=false
CHECK_ONLY=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --force) FORCE_BACKUP=true ;;
        --check) CHECK_ONLY=true ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# Confirm that every ROG Ally HID interface is bound to our driver ("asus"),
# not the stock in-kernel one ("asus_rog_ally", from hid-asus-ally.ko). This is
# the only way to know which driver actually owns the hardware: a loaded
# hid_asus module and a healthy interface binding are not the same thing - the
# stock module can silently reclaim interfaces (observed after a SteamOS
# update reset /etc/modprobe.d, see step 6) while ours stays loaded elsewhere.
# Returns 0 if every found interface is on "asus", 1 otherwise.
verify_binding() {
    local found=0 bad=0 dev real usbdev vid pid drv
    for dev in /sys/bus/hid/devices/*; do
        [ -e "$dev/uevent" ] || continue
        real=$(readlink -f "$dev")
        usbdev=$(dirname "$(dirname "$real")")
        vid=$(cat "$usbdev/idVendor" 2>/dev/null)
        pid=$(cat "$usbdev/idProduct" 2>/dev/null)
        # 0b05 = ASUSTeK; 1abe = ROG Ally, 1b4c = ROG Ally X
        case "$vid:$pid" in
            0b05:1abe|0b05:1b4c) ;;
            *) continue ;;
        esac
        found=1
        # An interface with no driver at all is the blacklist case: it looks
        # identical to broken hardware from userspace (no LEDs, no RGB menu),
        # so name it explicitly instead of skipping it.
        # Test the symlink, not -e: `readlink -f` on a missing driver link
        # returns the path it was handed, so basename yields the literal
        # string "driver" and an unbound interface reads as a bound one.
        if [ ! -L "$dev/driver" ]; then
            warn "  $(basename "$dev") has NO driver bound"
            bad=1
            continue
        fi
        drv=$(basename "$(readlink -f "$dev/driver")")
        # Identify ours by the module that owns the driver, not by the driver
        # name: the name can be changed while testing, and matching on it alone
        # reports our own build as the stock one.
        owner=$(basename "$(readlink -f "/sys/bus/hid/drivers/$drv/module" 2>/dev/null)" 2>/dev/null)
        if [ "$owner" = "${MODULE_NAME//-/_}" ]; then
            log "  $(basename "$dev") -> $drv (ours)"
        elif [ "$drv" = "asus_rog_ally" ]; then
            warn "  $(basename "$dev") is bound to the STOCK driver (asus_rog_ally), not ours"
            bad=1
        else
            warn "  $(basename "$dev") is bound to '$drv' (module ${owner:-unknown}), not ours"
            bad=1
        fi
    done
    if [ "$found" -eq 0 ]; then
        warn "No ROG Ally HID interfaces found - is the controller connected?"
        return 1
    fi
    [ "$bad" -eq 0 ]
}

# A leftover 'blacklist hid_asus' under /etc/modprobe.d stops our module
# loading at boot, which presents exactly like broken hardware. Match only our
# own module, so the intentional 'blacklist hid_asus_ally' is left alone.
check_blacklist() {
    local hits
    hits=$(grep -rlE "^[[:space:]]*blacklist[[:space:]]+${MODULE_NAME//-/[-_]}([[:space:]]|$)" \
        /etc/modprobe.d/ 2>/dev/null || true)
    [ -n "$hits" ] || return 0
    warn "Our module is blacklisted, so it cannot load at boot:"
    printf '%s\n' "$hits" | while read -r f; do
        warn "    $f"
    done
    warn "Remove the file(s) above and reboot."
    return 1
}

# --- 1. Environment Checks ---

log "Checking environment..."

# Check if running on SteamOS (rough check)
if [ ! -f /etc/steamos-release ] && [ ! -f /usr/bin/steamos-readonly ]; then
    warn "This script is designed for SteamOS. Proceed with caution."
fi

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
    error "Please run this script with sudo: sudo ./install.sh"
fi

if [ "$CHECK_ONLY" = true ]; then
    log "Checking driver binding (no build, no install)..."
    blacklisted=false
    check_blacklist || blacklisted=true
    if verify_binding; then
        log "All ROG Ally interfaces are bound to our driver."
        exit 0
    elif [ "$blacklisted" = true ]; then
        error "Our module is blacklisted (see above), so nothing can bind."
    else
        error "One or more interfaces are not on our driver. Re-run 'sudo ./install.sh' to fix."
    fi
fi

# Check if user password is set (required for pacman/sudo)
PW_STATUS=$(passwd --status "$TARGET_USER" | awk '{print $2}')
if [ "$PW_STATUS" != "P" ]; then
    log "--------------------------------------------------------"
    warn "User '$TARGET_USER' does not appear to have a password set."
    warn "SteamOS requires a user password for 'sudo' and 'pacman' operations."
    warn "If you haven't set one, run 'passwd' in a new terminal first."
    log "--------------------------------------------------------"
    read -p "Continue anyway? (y/N) " confirm
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# --- 2. Unlock Filesystem ---

if [ -f /usr/bin/steamos-readonly ]; then
    log "Ensuring filesystem is writeable..."
    steamos-readonly disable
fi

# --- 3. Initialize Keys ---

KEYRING_DB="/etc/pacman.d/gnupg/pubring.kbx"
# Check if keyring is missing, or if the DB file is empty/near-empty (32 bytes is typical for empty kbx)
if [ ! -d /etc/pacman.d/gnupg ] || [ ! -f "$KEYRING_DB" ] || [ $(stat -c%s "$KEYRING_DB" 2>/dev/null || echo 0) -le 32 ]; then
    log "Initializing/Populating pacman keys (this may take a minute)..."
    # Clear any stale GPG locks that might cause "not writable" errors
    rm -f /etc/pacman.d/gnupg/*.lock 2>/dev/null
    pacman-key --init
    pacman-key --populate archlinux holo
fi

# --- 4. Install Dependencies ---

log "Checking dependencies..."

# Detect correct headers
KERNEL_VER=$(uname -r)
# Extract common SteamOS header patterns (e.g., neptune-618)
HEADER_SUFFIX=$(echo "$KERNEL_VER" | grep -o 'neptune-[0-9]*' || echo "")
if [ -n "$HEADER_SUFFIX" ]; then
    HEADER_PKG="linux-${HEADER_SUFFIX}-headers"
else
    HEADER_PKG="linux-headers"
fi

log "Detected required headers: $HEADER_PKG"

# Install base-devel and headers
if [ -d "/lib/modules/$(uname -r)/build" ]; then
    log "Headers for running kernel $(uname -r) are already installed. Skipping headers package installation."
    pacman -Sy --needed --noconfirm base-devel zstd
else
    log "Installing matching headers ($HEADER_PKG) from repository..."
    if ! pacman -Sy --needed --noconfirm base-devel "$HEADER_PKG" zstd; then
        warn "Could not install $HEADER_PKG directly from repository. Trying archive URL..."
        KERNEL_VER_CLEAN=$(echo "$KERNEL_VER" | sed -E 's/([0-9]+)\.([0-9]+)\.([0-9]+)-([^-]+)-([0-9]+)-.*/\1.\2.\3.\4-\5/')
        EXACT_PKG_URL="https://steamdeck-packages.steamos.cloud/archlinux-mirror/jupiter-main/os/x86_64/${HEADER_PKG}-${KERNEL_VER_CLEAN}-x86_64.pkg.tar.zst"
        curl -s -f -L "$EXACT_PKG_URL" -o "/tmp/${HEADER_PKG}.pkg.tar.zst"
        pacman -U --noconfirm --config <(sed 's/SigLevel.*/SigLevel = Never/' /etc/pacman.conf) "/tmp/${HEADER_PKG}.pkg.tar.zst"
        rm -f "/tmp/${HEADER_PKG}.pkg.tar.zst"
    fi
fi


# --- 5. Build and Install ---

log "Detecting kernel headers..."
KVER=$(uname -r)
if [ -d "/lib/modules/${KVER}/build" ]; then
    log "Found build headers for running kernel: ${KVER}"
else
    log "Headers for running kernel ${KVER} not found. Searching for installed headers..."
    KVER=""
    for d in /lib/modules/*; do
        if [ -d "${d}/build" ]; then
            KVER=$(basename "${d}")
            log "Found active kernel headers: ${KVER}"
            break
        fi
    done
fi

if [ -z "${KVER}" ]; then
    error "Could not find any directory under /lib/modules/ containing a 'build' folder. Please install linux headers."
fi

KDIR="/lib/modules/${KVER}/build"
INSTALL_PATH="/lib/modules/${KVER}/kernel/drivers/hid/"

# Compiling needs no privileges, and building as root leaves root-owned
# objects behind that make the next unprivileged build fail in MODPOST with
# "Module.symvers: Permission denied". Reclaim whatever an earlier root build
# left, then run the compile itself as the user who invoked sudo.
AS_BUILDER=()
if [ "$TARGET_USER" != "root" ]; then
    AS_BUILDER=(runuser -u "$TARGET_USER" --)
    chown -R "$TARGET_USER" . 2>/dev/null || \
        warn "Could not reclaim ownership of build files in $(pwd); the build may fail."
fi

log "Building module using headers at ${KDIR}..."
"${AS_BUILDER[@]}" make clean KDIR="${KDIR}"
"${AS_BUILDER[@]}" make all KDIR="${KDIR}"

if [ ! -f "$MODULE_FILE" ]; then
    error "Build failed! $MODULE_FILE not found."
fi

log "Compressing module..."
"${AS_BUILDER[@]}" zstd -f "$MODULE_FILE"
# The Makefile only builds the stub on kernels whose asus-wmi lacks the
# hid-asus listener API; on the ones that have it the stub is a duplicate
# GPL export that modprobe refuses, so its absence here is the normal case.
if [ -f "$STUB_FILE" ]; then
    "${AS_BUILDER[@]}" zstd -f "$STUB_FILE"
fi

# Backup logic
mkdir -p "$INSTALL_PATH"
BACKUP_FILE="${INSTALL_PATH}/${MODULE_ZST}.bak"
TARGET_FILE="${INSTALL_PATH}/${MODULE_ZST}"

if [ -f "$TARGET_FILE" ]; then
    if [ ! -f "$BACKUP_FILE" ] || [ "$FORCE_BACKUP" = true ]; then
        log "Creating backup of original driver at $BACKUP_FILE..."
        cp -f "$TARGET_FILE" "$BACKUP_FILE"
    else
        log "Backup already exists at $BACKUP_FILE. Skipping backup."
    fi
fi

log "Installing to $INSTALL_PATH..."
cp -f "$MODULE_ZST" "$TARGET_FILE"
if [ -f "$STUB_ZST" ]; then
    cp -f "$STUB_ZST" "${INSTALL_PATH}/${STUB_ZST}"
elif [ -e "${INSTALL_PATH}/${STUB_ZST}" ]; then
    # A stub left by an install against an older kernel would still be listed
    # in modules.dep and would fail to insert here, taking hid-asus with it.
    log "Removing stale ${STUB_ZST} left by a previous install..."
    rm -f "${INSTALL_PATH}/${STUB_ZST}"
fi

log "Updating dependency map..."
depmod -a

# --- 6. Blacklist Stock Module ---

# SteamOS's atomic update process resets untracked /etc files (confirmed via
# journalctl: rauc-override.sh / holo-post-update-shutdown both log "Before
# /etc changes gets removed, a backup is created in
# /var/lib/steamos-atomupd/etc_backup" during an OS update). This file does
# not survive that, silently, so every write here is verified by reading it
# back rather than trusted.
BLACKLIST_FILE=/etc/modprobe.d/hid-asus-ally-blacklist.conf
log "Writing modprobe blacklist for stock hid_asus_ally..."
mkdir -p /etc/modprobe.d
echo 'blacklist hid_asus_ally' > "$BLACKLIST_FILE"
if [ "$(cat "$BLACKLIST_FILE" 2>/dev/null)" != "blacklist hid_asus_ally" ]; then
    error "Failed to write $BLACKLIST_FILE - it does not contain the expected line."
fi
log "Blacklist written and verified at $BLACKLIST_FILE"

# --- 7. Reload Module ---

if [ "${KVER}" != "$(uname -r)" ]; then
    warn "Module was built for kernel ${KVER} but you are running $(uname -r)."
    warn "Installation is complete for the new kernel. Please reboot your Steam Deck to use the new kernel and the module."
    exit 0
fi

log "Reloading module..."

# Tearing down a stock driver while its interfaces are still bound hard-resets
# this machine: no oops, nothing in pstore, the box power-cycles mid-command.
# Reproduced twice on 7.2 and isolated with tests/crash_isolate.sh, which also
# showed the safe order - unbinding each interface first runs the same remove()
# paths without incident, and the module teardown then completes normally.
#
# Two details make this worse than it looks. `modprobe -r hid_asus_ally` also
# removes hid_asus, because ally depends on it and modprobe cleans up
# dependencies that fall to zero users, so one command tore down both modules
# with their devices bound. And a driver removal releases its devices, which
# the remaining drivers immediately re-match - so the unbind has to be repeated
# between the two teardowns, not done once up front.
#
# This never fired before the 7.2 update only because our module was installed
# over the stock hid_asus, and the stock hid_asus_ally cannot load without it
# ("Unknown symbol validate_mcu_fw_version"). The OS update restored the stock
# module and wiped the blacklist, hid_asus_ally loaded for the first time, and
# the modprobe -r below finally had something real to unload.
unbind_ally_interfaces() {
    local d dev drv n=0
    for d in /sys/bus/hid/devices/0003:0B05:1ABE.* /sys/bus/hid/devices/0003:0B05:1B4C.*; do
        [ -e "$d/uevent" ] || continue
        [ -L "$d/driver" ] || continue
        dev=$(basename "$d")
        drv=$(basename "$(readlink -f "$d/driver")")
        if echo "$dev" > "/sys/bus/hid/drivers/$drv/unbind" 2>/dev/null; then
            n=$((n + 1))
        else
            warn "  Could not unbind $dev from $drv"
        fi
    done
    [ "$n" -gt 0 ] && log "  Unbound $n Ally interface(s)"
    return 0
}

# Our module and the in-kernel one are both called hid_asus, so no modprobe
# blacklist can tell them apart - and udev reloads the stock one from its
# modalias within seconds of the rmmod, re-binding interfaces before ours can
# be inserted. Pausing udev's event queue holds the window open. The trap
# matters: leaving the queue stopped after an error would break hotplug
# system-wide until the next boot.
UDEV_PAUSED=false
resume_udev() {
    [ "$UDEV_PAUSED" = true ] || return 0
    UDEV_PAUSED=false
    udevadm control --start-exec-queue 2>/dev/null \
        || warn "Failed to resume udev. Run: sudo udevadm control --start-exec-queue"
}
if command -v udevadm >/dev/null 2>&1; then
    if udevadm control --stop-exec-queue 2>/dev/null; then
        UDEV_PAUSED=true
        trap resume_udev EXIT
    else
        warn "Could not pause udev; the stock module may reload mid-swap."
    fi
fi

unbind_ally_interfaces
if lsmod | grep -q "^hid_asus_ally"; then
    # rmmod, not `modprobe -r`: no dependency cascade, so the two teardowns
    # stay separable and hid_asus is not removed out from under us here.
    rmmod hid_asus_ally || warn "  Could not unload hid_asus_ally."
fi

unbind_ally_interfaces
if lsmod | grep -q "^hid_asus\b"; then
    rmmod hid_asus || warn "  Could not unload the stock hid_asus."
fi

unbind_ally_interfaces
modprobe "${MODULE_NAME//-/_}"

resume_udev
trap - EXIT

log "Verifying installation..."
if lsmod | grep -q "${MODULE_NAME//-/_}"; then
    log "Module '$MODULE_NAME' loaded successfully."
else
    error "Module failed to load. Check 'dmesg' for details."
fi

log "Verifying driver binding on every ROG Ally interface..."
# A loaded module is not the same as owning the hardware: the stock driver
# can grab individual interfaces (e.g. the config/gamepad ones) while ours
# stays loaded elsewhere, and that only shows up by checking per-interface.
sleep 1
if ! verify_binding; then
    # A blacklist of our own module explains an otherwise baffling result:
    # the build and install both succeed, then nothing binds and the device
    # looks dead. Say which of the two it is rather than making it a guess.
    if ! check_blacklist; then
        error "Our module is blacklisted (see above). Remove the file(s) and reboot."
    fi
    error "One or more interfaces are not on our driver. Re-run this script; if it persists, reboot and re-run."
fi

# --- 7. Final Status ---

log "Installation complete!"
log "Checking dmesg for Ally X registration..."
dmesg | grep -i "asus" | tail -n 5 || true

log "Note: SteamOS updates wipe /etc/modprobe.d, which un-blocks the stock"
log "driver and lets it silently reclaim interfaces on the next probe. Always"
log "re-run 'sudo ./install.sh' after an OS update. Run 'sudo ./install.sh"
log "--check' any time to confirm binding without rebuilding."
