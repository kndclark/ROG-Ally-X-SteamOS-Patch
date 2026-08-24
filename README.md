# ROG Ally X Driver (`hid-asus`)

An enhanced build of the Linux `hid-asus` kernel driver for the **ASUS ROG Ally X**
(`0b05:1b4c`) on SteamOS, adding joystick-ring RGB control, SteamOS Game Mode
integration, and suspend/resume stability.

It builds on the upstream ROG Ally work by Denis Benato (NeroReflex) and Luke Jones.
Changes here are developed against, and periodically upstreamed into, that tree.

---

## What this adds

### Joystick-ring RGB

The Ally X's two joystick rings are exposed as a single multicolor LED device at
`/sys/class/leds/go_s:rgb:joystick_rings`, driven over the Aura `0x5A`/`0x5D`
protocol.

| Attribute | Purpose |
|---|---|
| `effect` / `effect_index` | Animation; readable list of what this device supports |
| `multi_intensity` / `multi_index` | Per-channel colour (`red green blue`) |
| `brightness` / `max_brightness` | Overall brightness, `0`-`100` |
| `speed` / `speed_range` | Animation speed, `0`-`100` |
| `direction` / `direction_index` | `forward` or `reverse` for directional effects |
| `enabled` / `enabled_index` | Output on/off |

Five effects are supported on this model: **`monocolor`, `breathe`, `chroma`,
`rainbow`, `strobe`**. The driver advertises them through `effect_index`, so
userspace never has to hardcode the list.

The LED registers as `LED_COLOR_ID_RGB` rather than the generic
`LED_COLOR_ID_MULTI`, so userspace can tell that the rings reproduce arbitrary
colours rather than merely having several channels.

**The rings start dark.** Zone state is pushed to the hardware as soon as the
device registers, so a lit default would light the rings on every cold boot
before the desktop restores the user's own settings. This matches `hid-playstation`,
which likewise leaves intensities at zero until userspace asks for something.

### SteamOS Game Mode integration

Steam's LED panel recognises devices by a fixed list of sysfs names and effect
strings, neither of which is documented. Two consequences shape this driver:

- The LED device is named `go_s:rgb:joystick_rings`. Steam enumerates exactly
  three paths and this is one of them; under any other name the RGB menu does
  not appear at all.
- The solid-colour effect is called `monocolor`. Steam only renders its colour
  picker for effect names it knows, and `monocolor` is on that list while
  `static` is not. `static` is still accepted when writing, for tools that use
  the wider ecosystem's vocabulary.

This vocabulary matches the upstream Lenovo Legion Go / Go S drivers, which is
the convention Valve's client is written against.

### Suspend and resume

- **Non-blocking init.** MCU setup runs from a `delayed_work` queue rather than
  sleeping in the resume path, so the driver never stalls kernel resume while
  waiting for the controller to become ready.
- **State restoration.** LED effect, colour, and brightness are re-applied on
  wake, which the MCU otherwise loses.
- **Button state reset.** Vendor buttons (Armoury Crate, Command Center) are
  force-released during re-initialisation so a key held across suspend cannot
  stick.

### Input handling

`asus_raw_event()` returns `-1` for Ally vendor reports it has fully handled.
Without this the same physical button is reported twice - once by the HID core
from the report descriptor, once by the driver - which userspace sees as a
double press.

---

## Requirements

- ROG Ally X (`0b05:1b4c`). Other Ally models are partially supported by the
  shared code paths but are not tested here.
- A password set for the `deck` user - SteamOS needs one for `sudo`.
- Clone somewhere under `/home/deck/` so the checkout survives SteamOS updates.

## Installing

```bash
cd ally_module
sudo ./install.sh
```

The script unlocks the read-only filesystem, installs build dependencies,
compiles the module out of tree against your running kernel, backs up the stock
driver, and reloads. Compilation runs as your own user, so the checkout stays
buildable afterwards.

Useful flags:

```bash
sudo ./install.sh --check     # report which driver owns each interface, change nothing
sudo ./uninstall.sh           # restore the stock driver from backup
```

> **After a SteamOS update:** system updates replace the kernel and revert
> drivers. Re-run `sudo ./install.sh` to reinstall.

### Checking it worked

```bash
sudo ./install.sh --check
cat /sys/class/leds/go_s:rgb:joystick_rings/effect_index
```

`--check` should report every ROG Ally interface bound to our driver. If an
interface shows no driver at all, the most common cause is a leftover
`blacklist hid_asus` in `/etc/modprobe.d/` - `--check` looks for that and says so.

---

## Differences from the kernel-tree version

This out-of-tree build is kept byte-identical to the upstream submission except
for four deliberate accommodations, so changes can move between the two freely:

| Difference | Reason |
|---|---|
| Local `asus-wmi.h` and `zenbook_compat.h` includes | Headers the SteamOS kernel does not ship |
| `kzalloc_obj(*attr, GFP_KERNEL)` | This kernel predates the single-argument form |
| `go_s:rgb:` LED name prefix | Steam's device allowlist; upstream uses `asus:rgb:` |
| `asus-wmi-stub` module | Satisfies WMI symbols without the full platform driver |

The LED name spoof is marked `NOT FOR UPSTREAM` in the source. It exists because
Valve's client matches on a hardcoded list; the correct long-term fix is for that
list to include the Ally.

---

## Credits

Built on the ASUS HID driver work of **Luke Jones**, **Denis Benato (NeroReflex)**,
and **Derek Clark (pastaq)**, and on protocol findings from the ROG Ally reverse-engineering
community.

Hardware protocol notes and captures live in the companion
[rog-ally-reverse-engineering](https://github.com/kndclark/rog-ally-reverse-engineering)
repository.
