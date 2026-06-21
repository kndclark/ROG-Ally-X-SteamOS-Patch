# Testing the usbhid "skip-poll-for-no-input-report" patch (PR62) on the ROG Ally X

**Patch under test:** `usbhid_custom`, a local build of the kernel `usbhid` driver carrying **Ghoul's PR62
change** — `|| list_empty(&hid->report_enum[HID_INPUT_REPORT].report_list)` added to the early-return in
`usbhid_open()`, so a HID interface with **no input reports** never gets an interrupt-IN polling URB armed.
Our only additions on top of PR62 are a log line, two `kmalloc_obj/kzalloc_obj` → `kmalloc/kzalloc` reverts
for this kernel, commenting out the duplicate `EXPORT_SYMBOL_GPL(hid_is_usb)`, and renaming the module to
`usbhid_custom` so it coexists with the built-in `usbhid`. On bypass it logs
`usbhid_custom PR62: Bypassing poll for device with no input reports!`.

**Hypothesis (from the asus-linux report):** on ASUS N-KEY keyboards, stock usbhid pointlessly polls the
RGB/feature-only interface (0 input reports), and that poll *interferes with the sibling keyboard
interface*, dropping keystrokes — "the input reports never reach the URB layer." The fix is to not poll
interfaces that can't produce input.

**Question for this unit:** does the ROG Ally X (ASUS N-KEY `0b05:1b4c`) exhibit the same interference,
and does the patch help?

**TL;DR result:** On this Ally X, arming the wasteful poll causes **no measurable input loss** on any
sibling interface — not on the buttons interface (~2,700 presses with the poll armed) and not on the
high-rate (~1 kHz) gamepad interface (tens of thousands of frames). **This unit does not reproduce the bug.**
The patch is still correct and structurally beneficial (it removes a pointless 1 ms poll on a no-input
endpoint), but its benefit here is structural, not functional.

Everything below was **read-only**: only `open(O_RDONLY)` + `read()` on input nodes and passive usbmon
captures, with no writes to the device.

---

## 1. Device under test — full topology

The Ally X exposes exactly **one** ASUS USB device: `1-2`, `0b05:1b4c` ("N-KEY Device"), USB device
number **5** at first (so `:005:` in usbmon; it re-enumerates to 6/7/8 as interfaces are bound and unbound,
so later sections show `:006:`/`:007:` and `Dev 008` — all tooling resolves the number live). `usbhid` is
**built into the kernel** (cannot be unloaded), and every interface is driven by `usbhid` at the transport
layer with `hid-asus` on top.

Enumerated by walking `/sys/class/hidraw/*/device` to the parent USB device, parsing each node's HID
report descriptor for Input/Output/Feature *main items*, and reading the endpoint descriptors:

| hidraw | USB iface | in/out/feat | interrupt-IN ep | bInterval | role |
|--------|-----------|-------------|-----------------|-----------|------|
| **hidraw1** | 1-2:1.0 | **0 / 0 / 13** | **ep5** (`ep_85`) | **1 ms** | **feature-only → PR62 target** |
| hidraw2 | 1-2:1.1 | 2 / 0 / 0 | ep2 (`ep_82`) | 4 ms | (no observed input from tested buttons) |
| hidraw3 | 1-2:1.2 | 4 / 1 / 3 | ep3 (`ep_83`) | 1 ms | N-KEY vendor (`0x5a`); M1/M2 paddles report here; **RGB/LED registered here** (see §1.1) |
| hidraw4 | 1-2:1.3 | 4 / 2 / 0 | ep1 (`ep_81`) | 1 ms | standard HID keyboard (paddles in distinct-key mode) |
| hidraw5 | 1-2:1.4 | 2 / 2 / 1 | ep6 (`ep_86`) | 4 ms | — |
| **hidraw7** | 1-2:1.5 | **10 / 6 / 0** | **ep7** (`ep_87`) | **1 ms** | **gamepad (analog sticks/buttons)** |

Two nodes are **not** on this device and therefore cannot be victims of an interference internal to one
device's firmware:

- `hidraw0` "ROG Ally X Controller" — a **Valve `uhid` virtual device** (`28DE:12FD` = Steam Input),
  i.e. the *processed* controller Steam presents, not the physical pad.
- `hidraw6` `NVTK0603` — the Novatek touchscreen (`hid-multitouch`), a separate device.

### 1.1 Why RGB/LED is on `1-2:1.2`, not on the feature-only `1-2:1.0` (hidraw1)

On the ASUS *laptops* where PR62 was originally motivated, the feature-only interface and the RGB
interface are reportedly the **same** HID node — so stopping its poll could theoretically affect LED
writes. On this Ally X they are **different** interfaces. Here is why, and how the driver makes the
distinction.

**The firmware decides the split, not the driver.** The Ally X MCU (`0b05:1b4c`) exposes **six** HID
interfaces on a single USB device (`1-2`), each with its own HID report descriptor and its own
interrupt-IN endpoint. Each interface is a separate `hid_device` in the kernel, probed independently by
hid-asus. Two of these are relevant:

| interface | endpoint | HID reports | what the MCU puts here |
|-----------|----------|-------------|------------------------|
| `1-2:1.0` (hidraw1) | `ep_85` (`0x85`) | 0 input / 0 output / **13 feature** | Pure config: feature reports only (MCU version queries, etc.). **No** input, **no** LED control packets. This is the PR62 target. |
| `1-2:1.2` (hidraw3) | `ep_83` (`0x83`) | 4 input / 1 output / **3 feature** | N-KEY vendor interface: carries `0x5a` button events (M1/M2 paddles) on the input side, **and** the LED config/set/apply feature reports (`0xb3`/`0xb4`/`0xb5`, code page `0xd1`) on the feature side. |

They are separate because the MCU's report descriptors define completely different report sets on each
interface — the `1.0` descriptor declares only feature reports (config queries), while the `1.2`
descriptor declares the mix of button-input reports *and* LED-control feature reports. The kernel
creates a separate `hid_device` for each, with a separate `hdev`, separate hidraw node, and separate
interrupt endpoint.

**How hid-asus dispatches between them.** `ally_get_endpoint_address()` reads the `bEndpointAddress`
from whichever interface the current `hdev` is bound to. `hid_asus_ally_probe()` switches on that
address:

- `case HID_ALLY_INTF_CFG_IN` (= `0x83`): this is the N-KEY/LED interface (`1-2:1.2`). The driver calls
  `ally_rgb_create(hdev)` here, stores `led_rgb->hdev = hdev`, and registers the `led_classdev_mc` under
  this `hdev`. All LED feature-report traffic (`ally_dev_set_report(led_rgb->hdev, ...)`) goes through
  this interface's USB pipe.
- `case HID_ALLY_INTF_KEYBOARD_IN` (= `0x81`): standard HID keyboard (`1-2:1.3`).
- `case HID_ALLY_X_INTF_IN` (= `0x87`): gamepad (`1-2:1.5`).
- Everything else (including `1-2:1.0` with `0x85`): falls through to `default` — hid-asus does nothing
  Ally-specific. The interface is still bound by the generic hid-asus/N-KEY path, but no LED, gamepad,
  or keyboard init runs for it.

So `1-2:1.0` is "feature-only" because the **MCU's descriptor** puts zero input reports there, and is
"not-RGB" because its endpoint (`0x85`) **doesn't match any Ally dispatch case** — the driver never runs
`ally_rgb_create()` for it. The LED lives on `1-2:1.2` because that is the interface whose endpoint
(`0x83`) matches `HID_ALLY_INTF_CFG_IN`, where the driver registers the LED.

**Consequence for PR62:** since PR62 acts on `1-2:1.0` (stopping its pointless ep5 poll) and the LED
is registered on `1-2:1.2` (a completely separate USB pipe, ep3), the patch cannot affect LED writes on
this Ally X. This is structurally different from the laptop topology where both functions share one
interface.

**Quick verification on a live system:**
```bash
# Confirm which interface owns which endpoint:
ls /sys/bus/usb/devices/1-2:1.0/ep_85/   # exists (feature-only node)
ls /sys/bus/usb/devices/1-2:1.2/ep_83/   # exists (N-KEY + LED node)
ls /sys/bus/usb/devices/1-2:1.0/ep_83/   # does not exist

# Confirm the LED classdev is parented to 1-2:1.2:
readlink -f /sys/class/leds/ally\:rgb\:joystick_rings/device
# → .../1-2:1.2/...
```

---

## 2. How we know we triggered the *correct* hidraw node (the PR62 target)

PR62 only changes behavior for an interface with **zero input reports**. We did **not** assume which node
that is (the original Discord one-liner keyed on interface `:1.1`; on this unit the feature-only node is
actually `:1.0`). We identified it three independent ways:

1. **Report-descriptor contents.** `hidraw1` (`1-2:1.0`) is the *only* node with **0 input / 0 output /
   13 feature** reports — exactly the "feature reports only" profile PR62 targets. Every other sibling has
   ≥2 input reports.

2. **It owns a real interrupt endpoint that stock usbhid will poll.** `hidraw1` has interrupt-IN endpoint
   `ep_85` with `bInterval=1` (1 ms). So stock usbhid arms a 1000 Hz poll on an endpoint that can never
   return input — the precise waste PR62 eliminates.

3. **We proved `cat /dev/hidraw1` actually arms that poll.** `cat`/our sniffer both do
   `open(O_RDONLY)` → `read()`; the `open()` is what calls `usbhid_open()`, which (on stock usbhid) submits
   the ep5 interrupt URB. After opening and then closing hidraw1, usbmon showed an ep5 URB **completion with
   status `-2` (ENOENT / unlinked)**:

   ```
   C Ii:1:005:5 -2:1 0
   ```

   A URB can only be *cancelled* if it was *submitted*. That cancellation is positive proof that opening
   hidraw1 had armed the ep5 interrupt poll. We also confirmed (scanning `/proc/*/fd`) that **nothing else
   held hidraw1 open** beforehand, so our `cat` was the sole, controlled trigger.

So the node we polled is unambiguously the no-input-report interface PR62 acts on.

---

## 3. How we know the joysticks are a *legitimate potential victim*

The reported interference is **internal to one physical device's firmware** (one interface's poll starving
a sibling). Therefore a valid victim must be **another interface on the same USB device as hidraw1**
(`1-2`, `0b05:1b4c`). The gamepad qualifies *only if* it lives on that device. We proved it does:

- **Same physical device.** Walking sysfs, `hidraw1,2,3,4,5,7` all resolve to `usb=1-2 0b05:1b4c`. The
  gamepad interface (`1-2:1.5`, ep7) is a **sibling of the PR62 target on the same device** — structurally a
  valid victim. (Steam's virtual pad `hidraw0` is a separate `uhid` device and was correctly excluded.)

- **The sticks really stream there.** Moving the left analog stick produced **16,484 usbmon URB lines on
  `:005:7`** in a single ~14 s window — 16-byte reports at ~1 kHz whose axis bytes change frame to frame:

  ```
  C Ii:1:005:7 0:1 16 = 0b008000 7f008000 80000000 00000000
  C Ii:1:005:7 0:1 16 = 0b008000 7c008000 80000000 00000000     (7f→7c = axis moving)
  ```

- **Why we measure it at the URB layer, not via hidraw.** During stick movement the *hidraw7* node returned
  **0** reports even though usbmon logged ~16k frames on ep7 — on this driver the Ally gamepad stream is
  delivered as the evdev gamepad, not on the raw hidraw node. (hid-asus handles the Ally vendor reports in
  its `asus_raw_event`, which returns `-1` to avoid double-mapping; see the hid-asus README.) usbmon at the
  URB layer captures every frame regardless — *the exact layer the original report points at* ("the input
  reports never reach the URB layer") — so that is where we looked for dropped frames.

The gamepad is also the **best** victim available: at ~1 kHz it delivers hundreds of frames per second,
giving far more statistical power to detect a subtle drop than discrete button presses can.

---

## 4. Experimental design

- **Single-variable, differential.** Identical stimulus measured with the ep5 poll **OFF** (baseline,
  hidraw1 closed) vs **ON** (armed, `cat /dev/hidraw1` held open in the background). Nothing else changes.
- **Read-only & non-destructive.** Only `open(O_RDONLY)`+`read()` on input nodes and passive usbmon, with
  no writes to the device.
- **Two independent victim interfaces:**
  - *Buttons* `hidraw3`/ep3 — M1/M2 paddle presses (`0x5a` vendor reports, keycode `0xa5`), mashed at a
    steady max rate for 60 s per run and counted, cross-checked against usbmon ep3 (see §5.1).
  - *Gamepad* ep7 — continuous ~1 kHz stick stream, analyzed at the URB layer for cadence/gaps, including a
    1.8–8 ms "missed-frame" bucket sensitive to *single* dropped frames (see §5.2).
- **Baseline family first.** For the gamepad we recorded a family of unarmed trials (n=7 in session 1, n=5
  in session 2) to characterize natural run-to-run variance *before* judging the armed run, so "is the armed
  run an outlier?" has a real reference; run-to-run CV is < 1% on every metric. The buttons test likewise
  used 3 unarmed baselines (the first a warmup) before the 10 armed runs.
- **Two independent observers.** Userspace `read()` counts on the hidraw node, cross-checked against
  kernel-level usbmon URB counts, on every keyboard run.

A note on `cat`: our sniffer's `watch`/`trial` modes are byte-for-byte the same kernel operation as
`cat /dev/hidrawN` — `open(O_RDONLY[ |O_NONBLOCK ])` then `read()`. `O_NONBLOCK` only affects whether the
*userspace* read blocks; it does not change the kernel's URB polling. So the sniffer is `cat` plus
timestamping/counting; it does not alter the experiment.

---

## 5. Results

### 5.1 Buttons interface (`hidraw3` / ep3), high-volume M1/M2 mashing

The Ally has no physical keyboard; the N-KEY device carries the special buttons as HID keyboard-style
reports on this interface. The buttons that reliably land here are the M1/M2 back paddles, which emit a
`0x5a` vendor report on each press and release. Stimulus: mash M1/M2 at a steady max rate for 60 s per run,
counting reports on ep3 (sniffer count cross-checked against usbmon ep3 completions). Baseline = poll OFF,
armed = poll ON (`cat /dev/hidraw1`, the same ep5 arming confirmed in §9).

| Condition | runs | presses/60s | total presses | usbmon ep3 completions/run | gaps > 0.5 s |
|-----------|------|-------------|---------------|----------------------------|--------------|
| Baseline (poll OFF) | 3 | ~267 (warmup run 236) | — | 550–551 (warmup 487) | 0 |
| Armed (poll ON) | 10 | 263–286, mean 272 | ~2724 | 542–590, mean 561 | 0 |

Armed throughput is at or above baseline (warmed past the first run), so no presses were lost — a dropped
report would lower the count. Two of the armed runs showed slightly longer pauses (switching to the off-hand
to rest the other, a less even press cadence), but their completion counts were normal, so those were pauses
rather than drops. No run, baseline or armed, had an inter-report gap over 0.5 s. About 2,700 presses went
through ep3 with the poll armed and nothing dropped.

*(The earlier low-volume runs (20/20, 23/23) reached the same conclusion; this high-volume set replaces them
as the primary buttons-side evidence. An even earlier run that looked lossy (~16/30) was a pacing/readiness
artifact at fast press rates, not a poll-induced drop, confirmed once pacing was controlled.)*

### 5.2 Gamepad interface (ep7), continuous ~1 kHz stream

Per 12 s capture we report: delivered-frame **rate**, **mean inter-frame gap**, the count of
**anomalous gaps > 3 ms** (multi-frame stalls / movement pauses), and — to catch *single*-frame drops that a
3 ms threshold misses — a **missed-frame bucket** of gaps in **1.8–8 ms** (one dropped frame turns a ~1 ms
cadence into ~2 ms). The reported **"active drop-rate" = missed / (normal + missed)**, excluding >8 ms
movement pauses. A genuine poll-induced drop must move two things *together*: **rate down** and
**missed-frame rate up**. (The absolute ~40% missed-frame rate is the natural cadence of circular motion —
the axis is unchanged for 2–3 polls — *not* real loss; it is the stable *reference* the armed run is judged
against.)

Two sessions reached the identical conclusion. The definitive run is **Session 2** (n=5 vs n=5, finer
metric; the bus had re-enumerated to usbmon `:006:` after a reboot, resolved dynamically):

| | rate /s | mean gap | missed-frame "active drop-rate" (1.8–8 ms) |
|---|---|---|---|
| **Baseline** (n=5, poll OFF) | 645 (641–649) | 1.55 ms | **40.51% ± 0.095%** (40.38–40.63) |
| **Armed** (n=5, poll ON) | 644 (636–648) | 1.55 ms | **40.62% ± 0.078%** (40.53–40.76) |

The missed-frame rate is statistically flat (overlapping ranges, means ~1σ apart) **and the delivery rate
does not move** (645 → 644) — so no frames were lost. A real drop would have pushed rate *down* and
missed-frame *up* together; instead rate is unchanged, so the 0.11-point blip is movement micro-variation
(the armed trials also caught a couple of larger repositioning pauses, e.g. a 56 ms gap). **Even a metric
that resolves 0.1% shows no interference.**

**Session 1 — coarse metric (corroborating, n=7 vs n=3, >3 ms only):** baseline 591 ± 5/s, mean gap
1.70 ± 0.014 ms, anomalies 228 ± 15; armed 599/s, 1.67 ms, 223 — i.e. armed sat at the *better* edge of the
baseline family across ~21,000 frames. (Those captures lived in tmpfs and were cleared by the reboot; the
summary numbers are retained here.) During Session 1 the ep5 arming was confirmed by an unlink completion
`C Ii:1:005:5 -2:1 0` when `cat` was killed — proof the poll had been armed (NAKs themselves aren't logged).

---

## 6. Conclusion

- Arming the wasteful ep5 poll produces **no measurable input loss** on this Ally X — on neither the
  keyboard interface nor the high-rate gamepad interface.
- **This unit (`0b05:1b4c`) does not reproduce the ASUS-laptop N-KEY poll-interference bug.** Its firmware
  tolerates the concurrent poll of the feature-only interface.
- **PR62 is still correct and worth landing:** it eliminates a pointless 1000 Hz poll on an endpoint that
  can never deliver input. On this device the benefit is *structural*, not a visible input fix — consistent
  with the patch author's own caveat that the set of affected devices isn't fully known. (We did not test a
  device that reproduces the drop, so this says nothing for or against the laptop reports.)
- **Structurally validated (§9):** with `usbhid_custom`, opening the feature-only node logs the bypass and
  arms no ep5 poll, vs stock usbhid which arms it. The patch does what it claims.
- **Scope on this unit:** the feature-only interface the patch acts on (`1-2:1.0`) is a *config* interface;
  the LED is registered on `1-2:1.2` (see §1.1 for the endpoint-address proof). So here the patch changes
  neither input nor RGB. This differs from the laptop topology, where the feature-only node *is* the RGB
  node — so the "RGB might break" regression question does not apply the same way on this Ally.

---

## 7. Limitations / threats to validity

- **Scope.** A negative result for *this* firmware/unit under the tested conditions; it does not refute the
  bug on the ASUS laptops where it was reported.
- **Single-frame sensitivity — addressed.** One dropped frame turns a ~1 ms gap into ~2 ms, below the 3 ms
  threshold, so we added the **1.8–8 ms "missed-frame" bucket**; it resolves 0.1% and still showed no
  armed-vs-baseline difference (§5.2). The aggregate **rate** is the corroborating guard — it stayed flat
  when armed, confirming no frames were lost.
- **Poll-active evidence for the armed captures was indirect** (unlink-completion + open-verification rather
  than logged NAKs) — but the definitive on/off demonstration is the **structural test in §9**, now done:
  with `usbhid_custom`, opening hidraw1 logs `PR62: Bypassing poll` and arms no ep5 poll, vs stock which arms
  it.
- **Movement-intensity coupling.** Gamepad rate depends on how vigorously the stick is moved; controlled by
  matching continuous motion across runs and by the tight baseline families (n=7 and n=5, CV < 1%). Button
  press *count* likewise varies with mashing speed and fatigue, which is why the buttons read on throughput
  (count never dipped below baseline) plus gaps > 0.5 s (zero), not on absolute count alone.

---

## 8. Tools & reproduction

- `tests/hidraw_sniff.py` — read-only multi-node hidraw sniffer.
  - `list` — enumerate nodes; parse report descriptors; flag the feature-only (PR62-target) node.
  - `watch [--node hidrawN …] [--duration S] [--quiet]` — timestamp/hex/count reports; classify down vs up.
  - `trial --node hidrawN --expect K` — counted-press loss test.
- `tests/usbmon_gaps.py <usbmon_file> [ep=7] [dev=005] [anomaly_ms=3.0]` — delivered-frame rate, inter-frame
  gap, the 1.8–8 ms missed-frame bucket / active drop-rate, and the >anomaly_ms stall count for a
  device:endpoint from a usbmon text capture. (`dev` is the usbmon device number; re-derive it from
  `/sys/bus/usb/devices/1-2/devnum` — the bus re-enumerates across reboots, e.g. 5 → 6.)

Reproduce (gamepad is ep7; feature-only target is the hidraw node for `1-2:1.0`):

```bash
# identify nodes + resolve the current usbmon device number
python3 tests/hidraw_sniff.py list
DEV=$(printf '%03d' "$(cat /sys/bus/usb/devices/1-2/devnum)")    # e.g. 006

# gamepad baseline (poll OFF) — move the left stick in continuous circles
sudo modprobe usbmon
sudo timeout 12 cat /sys/kernel/debug/usb/usbmon/1u > /tmp/base.txt
python3 tests/usbmon_gaps.py /tmp/base.txt 7 "$DEV"

# gamepad armed (poll ON)
cat /dev/hidraw1 >/dev/null &                                    # arms ep5 poll on stock usbhid
sudo timeout 12 cat /sys/kernel/debug/usb/usbmon/1u > /tmp/armed.txt  # move stick the same way
kill %1                                                          # disarm
python3 tests/usbmon_gaps.py /tmp/armed.txt 7 "$DEV"
```

---

## 9. Structural validation (done)

Installed `usbhid_custom` and did a **minimal targeted hot-swap**: moved *only* interface `1-2:1.0` (the
feature-only node) from `usbhid` to `usbhid_custom`, leaving the keyboard/gamepad on stock usbhid (no input
interruption; and the LED — registered on `1-2:1.2` — was never touched). hid-asus cleanly re-bound the
moved interface. (`usbhid` is built-in, so a per-interface hot-swap is the only way to put one interface on
the patched module; `usbhid_custom/install.sh` builds/installs cleanly, and `bind_custom.sh` was avoided as
too broad.)

Opening hidraw1 three times then produced, each time:

```
asus 0003:0B05:1B4C.001B: usbhid_custom PR62: Bypassing poll for device with no input reports!
```

and **usbmon showed 0 lines on ep5** during the opens — the 1 ms interrupt poll was **never armed**. Direct
before/after:

| transport on `1-2:1.0` | open hidraw1 → ep5 poll | dmesg |
|---|---|---|
| **stock usbhid** | **armed** (URB submitted; `-2` unlink-cancel on close, §5.2 / Step B) | — |
| **usbhid_custom (PR62)** | **not armed** (0 ep5 lines) | `PR62: Bypassing poll…` ×3 |

So the patch verifiably eliminates the wasteful poll on the no-input interface and announces it. Rolled back
cleanly afterward (`1-2:1.0` → usbhid, module unloaded). Note the device re-enumerated several
times during bind operations (devnum 5 through 8 over the session); all tooling resolves the device number
live, and the `PR62` log only emits from `usbhid_custom`, so the result is unaffected.
