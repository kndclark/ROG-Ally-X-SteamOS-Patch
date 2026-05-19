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
# INSTALL_PATH will be determined after detecting the kernel version
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
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --force) FORCE_BACKUP=true ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

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
if [ -f "/lib/modules/$(uname -r)/build/scripts/Kbuild.include" ]; then
    log "Headers for running kernel $(uname -r) are already installed. Skipping headers package installation."
    pacman -Sy --needed --noconfirm base-devel zstd
else
    # Try to download exact headers for the running kernel from the SteamOS archive
    KVER_BASE=$(echo $KERNEL_VER | cut -d'-' -f1)
    KVER_REL=$(echo $KERNEL_VER | cut -d'-' -f2,3)
    HEADER_PKG_NAME="${HEADER_PKG}-${KVER_BASE}.${KVER_REL}-x86_64.pkg.tar.zst"
    URL="https://steamdeck-packages.steamos.cloud/archlinux-mirror/jupiter-main/os/x86_64/${HEADER_PKG_NAME}"
    
    log "Attempting to download exact headers for $(uname -r) from SteamOS archive..."
    if curl --output /dev/null --silent --head --fail "$URL"; then
        log "Found exact headers! Downloading..."
        curl -L -o "/tmp/${HEADER_PKG_NAME}" "$URL"
        log "Installing (forcing overwrite of any broken leftover files)..."
        pacman -Sy --needed --noconfirm base-devel zstd
        pacman -U --noconfirm --overwrite "*" "/tmp/${HEADER_PKG_NAME}"
        rm -f "/tmp/${HEADER_PKG_NAME}"
    else
        warn "Exact headers not found on mirror ($URL)."
        warn "Falling back to pacman repository version (this may fetch headers for a newer pending OS update)..."
        pacman -Sy --needed --noconfirm base-devel "$HEADER_PKG" zstd
    fi
fi

# --- 5. Build and Install ---

log "Detecting kernel headers..."
KVER=$(uname -r)
if [ -d "/lib/modules/${KVER}/build" ]; then
    log "Found build headers for running kernel: ${KVER}"
else
    error "Headers for running kernel ${KVER} not found. You may need to reboot to apply pending SteamOS updates, or manually downgrade/install the headers matching your current kernel."
fi

KDIR="/lib/modules/${KVER}/build"
INSTALL_PATH="/lib/modules/${KVER}/kernel/drivers/hid/"

log "Building module using headers at ${KDIR}..."
make clean KDIR="${KDIR}"
make all KDIR="${KDIR}"

if [ ! -f "$MODULE_FILE" ]; then
    error "Build failed! $MODULE_FILE not found."
fi

log "Compressing module..."
zstd -f "$MODULE_FILE"
zstd -f "$STUB_FILE"

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
cp -f "$STUB_ZST" "${INSTALL_PATH}/${STUB_ZST}"

log "Updating dependency map..."
depmod -a

# --- 6. Reload Module ---

if [ "$KVER" = "$(uname -r)" ]; then
    log "Reloading module..."
    if lsmod | grep -q "${MODULE_NAME//-/_}"; then
        modprobe -r "${MODULE_NAME//-/_}"
    fi
    modprobe "${MODULE_NAME//-/_}"

    log "Verifying installation..."
    if lsmod | grep -q "${MODULE_NAME//-/_}"; then
        log "Module '$MODULE_NAME' loaded successfully."
    else
        error "Module failed to load. Check 'dmesg' for details."
    fi

    # --- 7. Final Status ---

    log "Installation complete!"
    log "Checking dmesg for Ally X registration..."
    dmesg | grep -i "asus" | tail -n 5 || true
else
    log "Module was built and installed for kernel ${KVER}."
    log "You are currently running kernel $(uname -r)."
    log "Please reboot your system to use the new module."
fi

log "Note: SteamOS updates will reset the filesystem. Simply re-run this script after an update to restore the driver."
