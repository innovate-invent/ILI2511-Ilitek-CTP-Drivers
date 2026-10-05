#!/usr/bin/env python3
# Print EDID identity of all DRM connectors, the USB strings of an ILITEK touch
# controller, and read-only vendor queries (0x42, 0x40, 0x61, 0x20, 0x21).
#
# Usage (needs root for /dev/hidraw*):
#   usb=$(for d in /sys/bus/usb/devices/*; do [ "$(cat $d/idVendor 2>/dev/null)" = 222a ] && readlink -f $d; done | head -1)
#   sudo python3 ili_identify.py "$usb"
#
# Commands sent are read-only queries on report ID 0x03 (header 0xA3).

import os, sys, glob, select, struct

print("== Display panel (EDID from DRM connectors) ==")
pnp = {}
for f in ("/usr/share/hwdata/pnp.ids", "/usr/share/misc/pnp.ids"):
    if os.path.exists(f):
        for l in open(f, errors="replace"):
            if "\t" in l: k, v = l.rstrip("\n").split("\t", 1); pnp[k] = v
        break
found = False
for e in sorted(glob.glob("/sys/class/drm/card*-*/edid")):
    d = open(e, "rb").read()
    if len(d) < 128: continue
    found = True
    conn = e.split("/")[-2]
    status = open(os.path.dirname(e) + "/status").read().strip()
    m = struct.unpack(">H", d[8:10])[0]
    mfg = "".join(chr(((m >> s) & 31) + 64) for s in (10, 5, 0))
    prod, serial = struct.unpack("<HI", d[10:16])
    print("%s (%s): manufacturer %s%s, product 0x%04x, serial %d, made week %d of %d, %dx%d cm"
          % (conn, status, mfg, " = " + pnp[mfg] if mfg in pnp else "", prod, serial, d[16], d[17] + 1990, d[21], d[22]))
    for o in (54, 72, 90, 108):
        b = d[o:o + 18]
        if b[:3] == b"\0\0\0" and b[3] in (0xFC, 0xFF, 0xFE):
            label = {0xFC: "name", 0xFF: "serial", 0xFE: "text"}[b[3]]
            print("    %-6s %s" % (label, b[5:].split(b"\n")[0].decode("ascii", "replace").strip()))
if not found: print("no EDID found (panel may be driven without EDID, e.g. DSI/LVDS via device tree)")

print("\n== Touch controller (USB) ==")
usb = sys.argv[1] if len(sys.argv) > 1 else ""
if not usb: sys.exit("ILI USB device not found")
for a in ("manufacturer", "product", "serial", "idVendor", "idProduct", "bcdDevice"):
    p = os.path.join(usb, a)
    if os.path.exists(p): print("%-12s %s" % (a, open(p).read().strip()))

nodes = sorted({os.path.basename(p) for p in glob.glob(usb + "/*:*/*/hidraw/hidraw*")})
fds = {n: os.open("/dev/" + n, os.O_RDWR | os.O_NONBLOCK) for n in nodes}

def query(cmd, rlen):
    pkt = bytes([0x03, 0xA3, 0x01, rlen, cmd] + [0] * 59)
    for w in nodes:
        try: os.write(fds[w], pkt)
        except OSError: continue
        for _ in range(200):
            ready = select.select(list(fds.values()), [], [], 1)[0]
            if not ready: break
            for fd in ready:
                r = os.read(fd, 64)
                if r[:3] == bytes([0x03, 0xA3, cmd]): return r[4:4 + r[3]]
    return None

def show(label, data, text_from=None):
    if data is None: print("%-22s no reply" % label); return
    s = " ".join("%02x" % b for b in data)
    if text_from is not None:
        t = data[text_from:].split(b"\0")[0].decode("ascii", "replace")
        s += '   "%s"' % t
    print("%-22s %s" % (label, s))

show("protocol (0x42)", query(0x42, 3))
show("firmware ver (0x40)", query(0x40, 8))
show("kernel/module (0x61)", query(0x61, 32), text_from=6)
show("panel info (0x20)", query(0x20, 15))
show("screen res (0x21)", query(0x21, 8))
