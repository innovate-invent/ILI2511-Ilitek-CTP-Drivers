#!/usr/bin/env python3
# Print the report IDs declared by each hidraw node of an ILITEK USB touch
# controller, then send the vendor Get_Protocol_Version command (0x42) and
# print the reply.
#
# Usage (needs root for /dev/hidraw*):
#   usb=$(for d in /sys/bus/usb/devices/*; do [ "$(cat $d/idVendor 2>/dev/null)" = 222a ] && readlink -f $d; done | head -1)
#   sudo python3 ili_protocol_version.py "$usb"
#
# Commands sent are read-only queries on report ID 0x03 (header 0xA3).

import os, sys, glob, select
usb = sys.argv[1]
nodes = sorted({os.path.basename(p) for p in glob.glob(usb + "/*:*/*/hidraw/hidraw*")})
if not nodes: sys.exit("no hidraw nodes under " + usb)

def report_ids(desc):
    # Walk HID short items; record which report IDs have Input/Output/Feature main items.
    ids, cur, page, i = {}, 0, None, 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item
            i += 3 + desc[i + 1]; continue
        n = (0, 1, 2, 4)[b & 3]; v = int.from_bytes(desc[i + 1:i + 1 + n], "little"); tag = b & 0xFC
        if tag == 0x04: page = v
        elif tag == 0x84: cur = v
        elif tag in (0x80, 0x90, 0xB0):
            ids.setdefault(cur, [page, set()])[1].add({0x80: "in", 0x90: "out", 0xB0: "feat"}[tag])
        i += 1 + n
    return ids

for n in nodes:
    desc = open("/sys/class/hidraw/%s/device/report_descriptor" % n, "rb").read()
    info = ", ".join("id 0x%02x page 0x%04x %s" % (k, p or 0, "/".join(sorted(t))) for k, (p, t) in sorted(report_ids(desc).items()))
    print("%s: %s" % (n, info))

fds = {n: os.open("/dev/" + n, os.O_RDWR | os.O_NONBLOCK) for n in nodes}
cmd = bytes([0x03, 0xA3, 0x01, 0x03, 0x42] + [0] * 59)
for w in nodes:
    try:
        os.write(fds[w], cmd)
    except OSError as e:
        print("write to %s failed: %s" % (w, e)); continue
    for _ in range(200):
        ready = select.select(list(fds.values()), [], [], 1)[0]
        if not ready: break
        for fd in ready:
            r = os.read(fd, 64)
            if r[:3] == bytes([0x03, 0xA3, 0x42]):
                rn = [k for k, v in fds.items() if v == fd][0]
                print("wrote %s, reply on %s: protocol version V%d.%d.%d" % (w, rn, r[4], r[5], r[6]))
                sys.exit(0)
    print("no reply after writing to %s" % w)
sys.exit("No reply on any node")
