savedcmd_hid-asus-ally.mod := printf '%s\n'   hid-asus-ally.o | awk '!x[$$0]++ { print("./"$$0) }' > hid-asus-ally.mod
