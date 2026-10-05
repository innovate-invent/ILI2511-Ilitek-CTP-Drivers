# ILI2511 touch controller: driver comparison and device investigation

This branch is a standalone report. It shares no history with `main`, which
holds the ILITEK driver packages this investigation started from.

**Question:** does the mainline Linux kernel support everything in the ILITEK
vendor driver (`Linux - Ubuntu 10.04~16.04/ilitek_limv5_9_0_1` on `main`),
especially I2C and the firmware mode switching the vendor driver offers for
active stylus pens? After that: what exactly is the touch device in hand, and
can it support an active pen?

**Short answer:**

- **I2C support:** present in the kernel. `ili210x.c` handles ILI251x chips
  with V3 firmware, and `ilitek_ts_i2c.c` handles newer V6 chips.
- **Mode switching:** not in the kernel. The vendor driver's command `0x68` is
  absent from both kernel drivers.
- **This device doesn't need either:**
  - It is connected over **USB HID**, so it is driven by `hid-multitouch`, not
    by an ILITEK I2C driver.
  - Its firmware is protocol **V3.1.0**. The vendor driver only sends the mode
    command to protocol **3.4.0 and later**.
  - Its HID descriptor declares a **finger-only** touch screen with no pen
    collection.
- **Active pen support would need different firmware from ILITEK.** None is
  publicly available. There is also no evidence the ILI2511 supports an active
  pen at all.

---

## Contents

1. [Kernel versus vendor driver](#1-kernel-versus-vendor-driver)
2. [The device under test](#2-the-device-under-test)
3. [Stylus support](#3-stylus-support)
4. [Firmware update](#4-firmware-update)
5. [Identifying the screen](#5-identifying-the-screen)
6. [Contacts and next steps](#6-contacts-and-next-steps)
7. [External resources](#7-external-resources)
8. [Files on this branch](#8-files-on-this-branch)
9. [Appendix A: ILITEK USB command channel](#appendix-a-ilitek-usb-command-channel)
10. [Appendix B: HID report descriptor decode](#appendix-b-hid-report-descriptor-decode)
11. [Appendix C: draft email to ILITEK](#appendix-c-draft-email-to-ilitek)

---

## 1. Kernel versus vendor driver

The comparison was made against Linux **7.3-rc6** (`a90ee4305`), checked out
as a shallow clone of `torvalds/linux`.

### 1.1 Which kernel driver handles which chip

The vendor driver supports two command protocols in one module:
`ilitek_read_data_and_report_3XX()` handles V3 and
`ilitek_read_data_and_report_6XX()` handles V6. Mainline splits them across two
drivers:

| Protocol | Touch data format | Kernel driver | DT compatible |
|---|---|---|---|
| V3 (older ILI251x firmware) | Command `0x10`, 5 bytes per point, big-endian X/Y, bit 7 = touching | `drivers/input/touchscreen/ili210x.c` | `ilitek,ili251x` (also `ili210x`, `ili2117`, `ili2120`) |
| V6 ("Lego" series) | Report ID `0x48`, 5–8 bytes per point, little-endian X/Y, contact count at byte 61 | `drivers/input/touchscreen/ilitek_ts_i2c.c` | `ilitek,ili2130/2131/2132/2316/2322/2323/2326/2520/2521`, ACPI `ILTK0001` |
| USB HID (any protocol) | Standard HID digitizer reports | `drivers/hid/hid-multitouch.c` (USB ID `222a:0001` is listed) | n/a |

`ilitek_ts_i2c.c` refuses V3 firmware at probe: `ilitek_protocol_init()`
returns `-EINVAL` with the comment "Protocol v3 is not support currently".

### 1.2 Feature comparison

| Vendor feature (limv5.9.0.1) | Mainline status |
|---|---|
| I2C transport, IRQ, reset GPIO, multitouch type B slots | **Yes**, in both drivers. `ili210x` can also poll when no IRQ line is wired. |
| Supply regulators (`vdd`, `vcc_i2c`) | **No.** Neither kernel driver requests regulators. |
| MTK DMA I2C path and per-platform glue (QCOM/MTK/Rockchip/Allwinner/Amlogic) | **No** (not needed in mainline). |
| **Function mode switch, command `0x68 55 AA <mode>`** (`/proc/ilitek/setmode_{0,1,2}`) | **No.** Neither driver sends `0x68`. |
| Test mode enter/exit around the mode switch (`F2 01`/`F2 00` on V3, `F0 03 00`/`F0 00 00` on V6) | **No.** |
| Firmware update | **Partly:** `ili210x` has a `firmware_update` sysfs attribute (loads `ilitek/ili251x.bin`, V3 only). `ilitek_ts_i2c` has none. |
| Firmware, kernel and protocol version, IC mode | **Yes**, as sysfs attributes in both drivers. |
| Calibrate (command `0xCC`) | **Yes**, in `ili210x` (`calibrate` attribute). |
| Pressure | **Partly:** `ili210x` reports `ABS_MT_PRESSURE` for ILI251x. `ilitek_ts_i2c` ignores V6 report formats 1–3 (pressure, width, height). |
| Sleep/wake commands on suspend/resume (`0x30`/`0x31`) | **Only** in `ilitek_ts_i2c`. `ili210x` has no power-management hooks. |
| Touch keys (command `0x22`) | **No.** |
| Click or double-click to wake (`ILITEK_GESTURE`) | **No.** |
| ESD watchdog (`ILITEK_ESD_PROTECTION`) | **No.** |
| Palm, thumb, water and mist status flags (`ILITEK_CHECK_FUNCMODE`, byte 31) | **No.** The vendor driver only logs them. |
| Production tests (open/short/uniformity, noise frequency) | **No.** |
| Vendor tool interfaces (`/proc/ilitek_ctrl` ioctl, debug nodes, netlink) | **No.** |
| Axis swap and inversion (compile-time macros) | **Yes**, via standard DT `touchscreen-*` properties (more flexible). |

### 1.3 How the vendor mode switch works

From `ilitek_protocol.c` (`api_protocol_set_funcmode`, line 659) and
`ilitek_tool.c` (line 1636):

1. Disable the IRQ, then enter test mode. On V3 that is `F2 01`. On V6 it is
   `F0 03 00`.
2. **Only if the protocol version is 3.4.0 or later** (`ptl.ver >= 0x30400`),
   send `68 55 AA <mode>`. Otherwise the driver logs "It is protocol not
   support" and stops.
3. Read command `0x80` (system busy) every 100 ms, up to 20 times, until it
   returns `0x50` (ready).
4. Read back `0x68` and check that byte 2 equals the requested mode.
5. Leave test mode (`F2 00` or `F0 00 00`), then re-enable the IRQ.

Things that affect what the modes are actually for:

- **The modes are not documented.** Neither the driver PDF nor the programming
  guide says what modes 0, 1 and 2 do.
- **The vendor driver has no stylus reporting at all.** Every contact goes
  through `ilitek_touch_down()` as `MT_TOOL_FINGER`, with no `BTN_TOOL_PEN`,
  hover or pen pressure.
- **The driver's own revision history points elsewhere.** It mentions "add
  glove mode control" (2019/05) and "Add switch modes function" (2019/08).
- **So do the kernel docs.** The kernel's ILI251x firmware-update commit
  describes switching "between different modes of operation of the touch
  surface, such as glove operation" by flashing firmware.

The modes are most likely sensing profiles (such as glove or water), not
active-pen enablement. This is inferred from those sources, not confirmed.

---

## 2. The device under test

All values below were read from the live device with the scripts in
[`tools/`](tools/).

### 2.1 USB identity

| Field | Value |
|---|---|
| USB ID | `222a:0001` (ILI Technology Corp.) |
| Manufacturer / product strings | `ILITEK` / `ILITEK-TP` (ILITEK defaults, no integrator branding) |
| bcdDevice | `0x0002` |
| Interfaces | `1.0` and `1.1`, both `usbhid` (`hidraw7`, `hidraw8`) |

### 2.2 Report IDs per interface

| Node | Report ID | Usage page | Direction | Purpose |
|---|---|---|---|---|
| hidraw7 | `0x04` | Digitizer (`0x0D`) | in | Touch data |
| hidraw7 | `0x06` | Digitizer / vendor | feature | Contact Count Maximum + 256-byte Windows certification blob (usage `0xFF00:0xC5`) |
| hidraw8 | `0x03` | Vendor (`0xFF00`) | in/out | **ILITEK command channel** |
| hidraw8 | `0x05` | Button (`0x09`) | in | Probably the mouse-emulation mode used by ILITEK's Windows tool (iUniTouch) |

### 2.3 Vendor command replies

| Command | Raw reply | Decoded |
|---|---|---|
| `0x42` protocol version | `03 01 00` | **V3.1.0** |
| `0x40` firmware version | `06 00 00 03 00 00 00 02` | 6.0.0.3.0.0.0.2 |
| `0x61` kernel version / module | `11 25 0d 00 01 01` + `ILI25110X3140O00` | MCU `0x2511` (**ILI2511**). The module name **`ILI25110X3140O00`** is ILITEK's project ID for this touch module. |
| `0x20` panel info | `80 25 80 25 27 19 0a 00 00 ff…` | Range 9600 × 9600, **39 × 25** electrode channels, 10 touch points, no buttons or keys |
| `0x21` screen resolution | `00 00 00 00 7f 25 7f 25` | 0–9599 on both axes |

A command written to hidraw7 was answered on hidraw8. The firmware accepts
commands on either interface, but the replies come back on interface 1.1.

### 2.4 Touch behaviour

- **Report rate:** about 125 Hz (`MSC_TIMESTAMP` advances 8000 µs per frame).
- **Corner check with `evtest`:**
  - Top-left read about (27, 2).
  - Bottom-right read about (9597, 9599).

  The touch layer is mapped across the whole display.

- **Physical size in the descriptor:** the HID descriptor gives 30.93 ×
  17.39 cm, which is 14″ 16:9. The real panel is 34.5 × 21.6 cm (16″ 16:10).
  The firmware's size fields are template values that were never customised.
  This doesn't affect touch, because the coordinates are scaled to the full
  screen.

---

## 3. Stylus support

### 3.1 HID descriptor

The 743-byte report descriptor ([`data/`](data/), decoded in
[Appendix B](#appendix-b-hid-report-descriptor-decode)) declares:

- **Present:** Touch Screen application collection, 10 × Finger logical
  collections, Contact Identifier, Tip Switch, X, Y, Scan Time, Contact Count,
  Contact Count Maximum.
- **Absent:**
  - Pen (`0x0D:0x02`) and Stylus (`0x0D:0x20`) collections
  - In Range (hover)
  - Tip Pressure
  - Barrel Switch, Eraser and Invert
  - Width, Height and Confidence

### 3.2 Conclusions

- **With this firmware, an active stylus can only appear as a finger.** That is
  if the sensor detects it at all. There is no hover, pressure or button
  support.
- **Linux is missing nothing for this device.** `hid-multitouch` reports
  exactly what the descriptor declares, and the vendor driver also reports
  everything as fingers.
- **The vendor mode switch would not run.** Firmware 3.1.0 is below the 3.4.0
  minimum.
- **The ILI2511 is not marketed for pens.** ILITEK positions it for POS, ATM
  and industrial panels. None of the datasheet summaries found mention stylus
  support.
- **ILITEK's working pen support uses a separate report, not a mode switch.** A
  2026 kernel patch series adds stylus support to `ilitek_ts` for the CHUWI
  Hi10 Max. Its firmware sends pen events in their own report ID (`0x0c`), with
  pressure in `buf[6..7]` and side buttons in `buf[1]`. That is a V6 I2C part,
  not a V3 ILI2511.
- **Practical test:** run `sudo libinput debug-events` and draw with the pen.
  Touch events mean the pen works today as a finger. Nothing means this
  firmware doesn't sense it.

---

## 4. Firmware update

- **The protocol supports flashing over USB.**
  - The ILITEK programming guide on `main`, section 6.1 "USB Programming
    Command Flow", documents the sequence on report `0x03`/`0xA3`: write
    enable `C4 5A A5`, switch to bootloader `C2`, 32-byte data writes `C3`,
    CRC check `C7`/`CD`, switch back to application mode `C1`, and mode check
    `C0`.
  - The vendor's Windows tool, iUniTouch (on `main` under
    `Windows 10, 8.1, 7, XP/`), has a "FW Upgrade" page that takes a `.hex`
    file.
- **Linux has no USB flashing path.**
  - `hid-multitouch` has no update support.
  - `ili210x`'s `firmware_update` only works over I2C.
  - A hidraw-based userspace tool could be written from the programming guide,
    but it hasn't been, and it would be untested.
- **No firmware image is publicly available**, at 3.4.0+ or any other
  version.
  - ILITEK doesn't publish ILI2511 images.
  - Distributor pages carry only datasheets.
  - `linux-firmware` ships nothing for ILITEK.
  - The module name `ILI25110X3140O00` has no public matches.
- **Risk:** firmware is tuned per sensor. Flashing an image from another module
  can break touch, or leave the controller stuck in its bootloader (`0xC0`
  returns `0x55`). Get the image and a recovery procedure from ILITEK or the
  module supplier.

---

## 5. Identifying the screen

| Source | Finding |
|---|---|
| Shopee listing | Sold as a "BOE" 2.5K portable monitor ("[Tặng kèm bao da] BOE Màn hình di động IPS FHD 2.5K 60hz 144hz Type-C HDMI Portable Monitor"), shop ID 966994796, item 24900319500. "BOE" names the panel maker, not the monitor's brand. |
| EDID on `card0-DP-3` (the touch monitor) | `RTK` (Realtek scaler) with placeholder data: product `0x0000`, serial `0x01010101`, week 0 of 2023, 0 × 0 cm, no name string. |
| DDC/CI on DP-3 | Model string "RTK", MCCS 2.2. VCP `C8` = RealTek controller with all ID fields zero. VCP `C9` firmware level 0.1. Only basic controls are supported. |
| Native mode on DP-3 | **2560 × 1600** (16:10) |
| Measured image width | **34.5 cm**, which means a **16.0″** panel |
| Panel | **BOE 16.0″ 2560×1600, most likely the NE160QDM family.** The exact suffix (such as NY1 or NZ1) is only on the panel's sticker. |
| EDID on `card1-eDP-1` | Host laptop panel `TL156VDXP0101`, a Tianma 15.6″ panel (PNP code `TMX`). Not the touchscreen. |

**Overall:** the monitor is an unbranded assembly. It combines a BOE 16″ 2.5K
panel, a generic Realtek scaler board, and a touch module built around an
ILITEK ILI2511 chip. All three are running default or template firmware.

---

## 6. Contacts and next steps

### 6.1 ILITEK contacts

| Contact | Detail | Source |
|---|---|---|
| Bert Chang, Product/Sales Manager | `bert_chang@ilitek.com` | 2023 press release (Andes / GlobeNewswire) |
| ILITEK head office (current) | 10F., No. 1, Sec. 3, Gongdao 5th Rd., East Dist., Hsinchu City 300042, Taiwan. Tel +886-3-5726533 | same press release |
| ILITEK head office (older) | 10F, No. 1, Taiyuan 2nd St., Zhubei City, Hsinchu County 302, Taiwan. Tel +886-3-5600099 | programming guide and iUniTouch guide on `main` |
| Luca Hsu (author of the vendor driver) | `luca_hsu@ilitek.com` | GPL headers in `ilitek_limv5_9_0_1` |
| Joe Hung (author of the mainline `ilitek_ts_i2c.c`) | `joe_hung@ilitek.com` | GPL header in the kernel driver |
| Distributor | WPG Holdings / Yosun lists ILITEK as a supplier | wpgholdings.com |

`www.ilitek.com` did not resolve from the environment used for this
investigation.

### 6.2 Next steps

1. **Read the panel sticker** to get the exact BOE part number, and the touch
   flex-cable (FPC) marking.
2. **Email ILITEK** using [Appendix C](#appendix-c-draft-email-to-ilitek).
   Send a copy to the Shopee seller as well, asking who made the touch module
   and whether a stylus is supported. Newhaven Display or WPG/Yosun are
   alternative routes.
3. **Test a passive capacitive stylus**, which should work as a finger today.
4. **If pen-capable firmware is obtained:**
   - Flash it with iUniTouch on Windows.
   - Re-dump the HID descriptor on Linux.
   - If a Pen collection appears, `hid-multitouch` should handle it with no
     driver changes.

---

## 7. External resources

### Linux kernel

- `ili210x.c` (V3 ILI251x I2C driver):
  https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/input/touchscreen/ili210x.c
- `ilitek_ts_i2c.c` (V6 I2C driver):
  https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/input/touchscreen/ilitek_ts_i2c.c
- `hid-multitouch.c` (USB HID path, ILITEK `222a:0001` entry):
  https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/hid/hid-multitouch.c
- "Input: ili210x - add ili251x firmware update support" (hex format, `ihex2fw`
  conversion, glove-mode note):
  https://linux.googlesource.com/linux/kernel/git/dtor/input/+/c6ac8f0b4ca927316eb40e1e9ba83df5d29f3793%5E%21/
  and the patch posting: https://patches.linaro.org/patch/504782
- Original ILI251x driver submission (2018):
  https://lkml.iu.edu/hypermail/linux/kernel/1805.0/04637.html
- "Input: ilitek_ts: add stylus input support" (CHUWI Hi10 Max, report ID
  `0x0c`, 2026):
  https://ratatoskr.run/lkml/2026/06/17182409/t and
  https://lkml.iu.edu/2607.1/04846.html
- "input: touchscreen: Add ilitek touchscreen driver support" (2025):
  https://lists.openwall.net/linux-kernel/2025/11/19/1917

### ILITEK documentation and datasheets

- ILI2511 datasheet (Crystalfontz): https://www.crystalfontz.com/controllers/Ilitek/ILI2511
- ILI2511 (Orient Display): https://orientdisplay.com/controller-datasheets/llitek/ili2511-capacitive-touch-screen-controller/
- ILI2511 (Topway): https://www.topwaydisplay.com/IC-Datasheet/ILI2511-Single-Chip-Capacitive-Touch-Sensor-Controller
- ILI2511 (DLC Display): https://www.dlcdisplay.com/Downloaddetail/90.html
- ILITEK TP programming guide for ILI2130/ILI252x (V1.50):
  https://www.lcd-module.de/eng/pdf/zubehoer/ILI2130_Programming_Guide_V1_50.pdf
- iUniTouch Tool user guide (newer V1.08):
  https://www.agneovo.com/wp-content/uploads/2024/09/iUniTouch_Tool_User_Guide.pdf

### Other ILI2511 code

- Newhaven Display driver packages (upstream of `main`):
  https://github.com/NewhavenDisplay/ILI2511-Ilitek-CTP-Drivers
- Infineon ModusToolbox ILI2511 driver (MCU driver, no firmware):
  https://github.com/Infineon/touch-ctp-ili2511

### Contacts

- Andes / IAR press release naming ILITEK's sales contact:
  https://www.andestech.com/en/2023/02/15/andes-and-iar-systems-together-enable-leading-vendor-ilitek-to-accelerate-the-development-of-its-iso-26262-ready-tddi-soc-ili6600a/
- Same release on GlobeNewswire:
  https://www.globenewswire.com/news-release/2023/02/14/2607888/0/en/Andes-and-IAR-Systems-Together-Enable-Leading-Vendor-ILITEK-to-Accelerate-the-Development-of-its-ISO-26262-Ready-TDDI-SoC-ILI6600A.html
- WPG Holdings / Yosun ILITEK page:
  https://www.wpgholdings.com/yosung/subsidiary/en/Yosun/Ilitek

### Product

- Shopee listing for the monitor:
  https://shopee.vn/-T%E1%BA%B7ng-k%C3%A8m-bao-da-BOE-M%C3%A0n-h%C3%ACnh-di-%C4%91%E1%BB%99ng-IPS-FHD-2.5K-60hz-144hz-Type-C-HDMI-Portable-Monitor-i.966994796.24900319500

---

## 8. Files on this branch

| Path | Description |
|---|---|
| `README.md` | This report |
| `tools/ili_protocol_version.py` | Lists the report IDs on each hidraw node of the ILITEK device, then reads the protocol version (`0x42`), listening for the reply on every node |
| `tools/ili_identify.py` | Prints EDID identity for all DRM connectors, the USB strings, and read-only vendor queries `0x42`, `0x40`, `0x61`, `0x20`, `0x21` |
| `data/hidraw7_report_descriptor.bin` | The touch interface's HID report descriptor (743 bytes) |
| `data/hidraw7_report_descriptor.xxd` | The same descriptor as an `xxd` hex dump |

Both scripts need root to open `/dev/hidraw*`, and take the USB device's sysfs
path as their only argument. See the header of each script for usage. They
only send read-only queries.

---

## Appendix A: ILITEK USB command channel

From section 4.2 of the programming guide. The I2C commands are wrapped in
64-byte HID reports with report ID `0x03`:

```
OUT (host -> device):  03 A3 <write_len> <return_len> <cmd> <args...>   (padded to 64 bytes)
IN  (device -> host):  03 A3 <cmd> <return_len> <data...>
```

Example from this device:

```
OUT  03 a3 01 03 42           Get_Protocol_Version, 1 byte written, 3 bytes expected
IN   03 a3 42 03 03 01 00     V3.1.0
```

On Linux, write the OUT report to `/dev/hidrawN` and read the IN report from
the vendor interface (`hidraw8` here). Touch reports arrive on the other
interface, so filter replies by the `03 A3 <cmd>` header.

## Appendix B: HID report descriptor decode

Summary of `data/hidraw7_report_descriptor.bin`:

```
Usage Page (Digitizer), Usage (Touch Screen), Collection (Application)
  Report ID (0x04)
  10 x {
    Usage (Finger), Collection (Logical)
      Contact Identifier   6 bits, 0..63
      Tip Switch           1 bit
      padding              1 bit
      Usage Page (Generic Desktop)
      X  16 bits, logical 0..9600, physical 0..3093 x 10^-2 cm
      Y  16 bits, logical 0..9600, physical 0..1739 x 10^-2 cm
    End Collection
  }
  Scan Time        32 bits
  Contact Count     8 bits (max 127)
  padding          64 bits
  Report ID (0x06), Feature
    Contact Count Maximum  8 bits (max 10)
    Usage Page (Vendor 0xFF00), Usage (0xC5)  256 bytes  (Windows certification blob)
End Collection
```

Usages present: `0001:30` (X), `0001:31` (Y), `000d:04` (Touch Screen),
`000d:22` (Finger), `000d:42` (Tip Switch), `000d:51` (Contact ID),
`000d:54` (Contact Count), `000d:55` (Contact Count Maximum), `000d:56`
(Scan Time), `ff00:c5` (vendor blob).

## Appendix C: draft email to ILITEK

> **To:** bert_chang@ilitek.com
> **Cc:** luca_hsu@ilitek.com
> **Subject:** ILI2511 module ILI25110X3140O00: active stylus support and firmware availability
>
> Dear Mr. Chang,
>
> I'm using a portable touch monitor with an ILITEK ILI2511 touch controller and would like to know whether it can support an active stylus. The monitor was sold on Shopee Vietnam as a "BOE" 2.5K portable monitor (shop ID 966994796, item 24900319500), using a BOE 16″ 2560×1600 panel behind a Realtek scaler. The touch module itself carries no branding, so I can't trace its supplier and am contacting ILITEK directly. I'd be grateful if you could forward this to the right technical contact.
>
> **Device details (read from the controller):**
> - Module name (command 0x61): `ILI25110X3140O00`
> - MCU: ILI2511 (0x2511)
> - Firmware version (command 0x40): `06 00 00 03 00 00 00 02`
> - Protocol version (command 0x42): 3.1.0
> - Interface: USB HID, VID/PID `222a:0001`, bcdDevice 0x0002
> - Panel info (command 0x20): 9600 × 9600 range, 39 × 25 channels, 10 touch points, no keys
> - Display: BOE 16.0″ 2560 × 1600 (16:10), probably the NE160QDM series, with an active area of about 34.5 × 21.6 cm
>
> Touch works correctly across the whole screen, from about (27, 2) at the top-left to (9597, 9599) at the bottom-right, reported at about 125 Hz. The HID report descriptor declares a finger-only touch screen: 10 contacts, Tip Switch, Contact ID, X and Y. There is no Pen collection, In Range, Tip Pressure or Barrel Switch usage.
>
> **My questions:**
> 1. Does the ILI2511 with this sensor support an active stylus (hover, pressure, buttons)? If so, which pen protocols or stylus models are compatible?
> 2. Is there firmware for this module that adds pen reporting? This could be a HID descriptor with a Pen collection, or protocol 3.4.0 or later with a pen mode that can be selected with command 0x68.
> 3. In your Linux driver (limv5.9.0.1), what do function modes 0, 1 and 2 (`/proc/ilitek/setmode_N`) correspond to on the ILI2511?
> 4. If new firmware is available, can it be flashed over USB with iUniTouch Tool, and how do I recover if the update fails?
> 5. The HID descriptor reports a physical size of 30.93 × 17.39 cm, but the sensor covers a 16″ panel (34.5 × 21.6 cm). Is a firmware version with corrected values available?
>
> If active pen isn't supported on this hardware, please let me know. That would settle the question for me.
>
> I'm using Linux, and can provide the full HID report descriptor or any other command output you need.
>
> Thank you for your help.
>
> Best regards,
> [Your name]
