#!/bin/bash

# ROG Ally X LED Module Installer for SteamOS
# This script automates building and installing the hid-asus-ally module.

set -e

# --- Configuration ---
MODULE_NAME="hid-asus-ally"
MODULE_FILE="${MODULE_NAME}.ko"
MODULE_ZST="${MODULE_FILE}.zst"
INSTALL_PATH="/lib/modules/$(uname -r)/kernel/drivers/hid/"
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
pacman -Sy --needed --noconfirm base-devel "$HEADER_PKG" zstd

# --- 5. Build and Install ---

log "Building module..."
make clean
make all

if [ ! -f "$MODULE_FILE" ]; then
    error "Build failed! $MODULE_FILE not found."
fi

log "Compressing module..."
zstd -f "$MODULE_FILE"

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

log "Updating dependency map..."
depmod -a

# --- 6. Reload Module ---

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
dmesg | grep -i "asus_rog_ally" | tail -n 5 || true

log "Note: SteamOS updates will reset the filesystem. Simply re-run this script after an update to restore the driver."
