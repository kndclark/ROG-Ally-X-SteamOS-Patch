#!/bin/bash
# Step 1: return the Ally to stock HID drivers after the 7.2 install crash.
#
# The crashed install.sh run left three things behind that together make the
# device unusable: our (untested on 7.2) hid-asus.ko.zst installed over the
# stock one, a 0-byte asus-wmi-stub.ko.zst truncated by the hard reset, and a
# modprobe blacklist that blocks the stock hid_asus_ally. With our module
# unloadable and the stock one blacklisted, every Ally interface falls through
# to hid-generic: no LEDs, no gamepad, InputPlumber refuses to attach.
#
# This undoes all three and leaves the machine on unmodified stock drivers.
# It changes nothing in the repo and loads no modules - the state it restores
# takes effect on the next boot.

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()   { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[ "$EUID" -eq 0 ] || error "Run with sudo: sudo ./tests/recover_stock.sh"

KVER=$(uname -r)
HIDDIR="/usr/lib/modules/${KVER}/kernel/drivers/hid"
TARGET="${HIDDIR}/hid-asus.ko.zst"
BACKUP="${TARGET}.bak"
STUB="${HIDDIR}/asus-wmi-stub.ko.zst"
BLACKLIST="/etc/modprobe.d/hid-asus-ally-blacklist.conf"

log "Kernel: ${KVER}"
[ -d "$HIDDIR" ] || error "No such directory: $HIDDIR"

# --- Verify the backup before trusting it -------------------------------------
# Never overwrite the installed module on the word "it's the backup": confirm
# the file really is the stock, kernel-signed hid-asus built for THIS kernel.
# A bad restore here leaves no working driver at all.
[ -f "$BACKUP" ] || error "No backup at $BACKUP - nothing to restore from."

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

zstd -d -q -f "$BACKUP" -o "$TMP/bak.ko" \
    || error "$BACKUP does not decompress - it is not a usable module."

BAK_VERMAGIC=$(modinfo "$TMP/bak.ko" | awk '/^vermagic:/{print $2}')
BAK_SIGNER=$(modinfo "$TMP/bak.ko" | sed -n 's/^signer:[[:space:]]*//p')
BAK_DEPENDS=$(modinfo "$TMP/bak.ko" | sed -n 's/^depends:[[:space:]]*//p')

[ "$BAK_VERMAGIC" = "$KVER" ] \
    || error "Backup vermagic '$BAK_VERMAGIC' != running kernel '$KVER'. Refusing to restore."
[ -n "$BAK_SIGNER" ] \
    || error "Backup carries no module signature - that is not the stock module. Refusing to restore."
case "$BAK_DEPENDS" in
    *asus-wmi-stub*) error "Backup depends on asus-wmi-stub - that is OUR build, not stock. Refusing." ;;
esac

log "Backup verified: stock hid-asus, vermagic ${BAK_VERMAGIC}, signed by '${BAK_SIGNER}'"

# --- Make the rootfs writable -------------------------------------------------
if [ -f /usr/bin/steamos-readonly ]; then
    log "Ensuring filesystem is writeable..."
    steamos-readonly disable || warn "steamos-readonly disable returned non-zero (may already be rw)"
fi

# --- 1. Restore the stock module ----------------------------------------------
if [ -f "$TARGET" ]; then
    CUR_SIZE=$(stat -c%s "$TARGET")
    log "Replacing installed hid-asus.ko.zst (${CUR_SIZE} bytes) with the stock backup..."
else
    warn "$TARGET is missing entirely; restoring from backup."
fi
cp -f "$BACKUP" "$TARGET"
# Keep the .bak in place: install.sh's backup logic checks for its existence,
# and a second backup pass would otherwise snapshot a non-stock module.

# --- 2. Drop the truncated stub -----------------------------------------------
# The stub is obsolete on 7.2 regardless of its size: this kernel's asus_wmi
# exports asus_hid_register_listener / _unregister_listener / asus_hid_event
# itself, so the stub is a duplicate-symbol conflict. Removing it also removes
# the 0-byte file that is currently the only thing stopping our module from
# autoloading at boot.
if [ -e "$STUB" ]; then
    log "Removing obsolete asus-wmi-stub.ko.zst ($(stat -c%s "$STUB") bytes)..."
    rm -f "$STUB"
else
    log "No asus-wmi-stub.ko.zst present - nothing to remove."
fi

# --- 3. Unblock the stock Ally driver -----------------------------------------
if [ -f "$BLACKLIST" ]; then
    log "Removing $BLACKLIST so stock hid_asus_ally can load..."
    rm -f "$BLACKLIST"
else
    log "No blacklist file present."
fi

# --- 4. Rebuild the dependency map --------------------------------------------
log "Running depmod -a..."
depmod -a

# --- 5. Verify ----------------------------------------------------------------
log "Verifying restored state..."
FAIL=0

RESTORED_SIGNER=$(zstd -d -q -f "$TARGET" -o "$TMP/now.ko" && modinfo "$TMP/now.ko" | sed -n 's/^signer:[[:space:]]*//p')
if [ -n "$RESTORED_SIGNER" ]; then
    log "  hid-asus.ko.zst is the signed stock module ('$RESTORED_SIGNER')"
else
    warn "  hid-asus.ko.zst is NOT signed - restore did not take"; FAIL=1
fi

if [ -e "$STUB" ]; then warn "  asus-wmi-stub.ko.zst still present"; FAIL=1
else log "  asus-wmi-stub.ko.zst is gone"; fi

if [ -f "$BLACKLIST" ]; then warn "  blacklist file still present"; FAIL=1
else log "  hid_asus_ally is no longer blacklisted"; fi

DEP=$(modprobe --dry-run -v hid_asus 2>&1 || true)
if echo "$DEP" | grep -q 'asus-wmi-stub'; then
    warn "  modules.dep still pulls in asus-wmi-stub:"; echo "$DEP" | sed 's/^/      /'; FAIL=1
else
    log "  modprobe hid_asus no longer pulls in the stub"
fi

echo
if [ "$FAIL" -ne 0 ]; then
    error "Recovery incomplete - see the warnings above."
fi

log "Recovery complete. REBOOT now to bind the stock drivers:"
log "    sudo reboot"
echo
log "After the reboot, confirm stock ownership with:"
log "    for d in /sys/bus/hid/devices/0003:0B05:1B4C.*; do \\"
log "        echo \"\$(basename \$d) -> \$(basename \$(readlink -f \$d/driver 2>/dev/null))\"; done"
log "Expect 'asus' on most interfaces and 'asus_rog_ally' on .0003 and .0006."
