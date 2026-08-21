#!/bin/bash
#
# Decides whether a save format can be bridged by renaming alone.
#
# Adding a row to MAPPINGS in save-bridge.sh is a claim that two emulators
# write byte-compatible saves and differ only in the file extension. That claim
# has to be checked against real saves before it is trusted, because getting it
# wrong silently corrupts somebody's progress. This is the check.
#
#   ./save-format-check.sh <file>              classify one save
#   ./save-format-check.sh <file-a> <file-b>   compare a pair from two emulators
#
# Give it the SAME GAME saved in both emulators, each with real progress. Two
# empty saves will agree perfectly and prove nothing, so that case is rejected
# outright -- it is the easiest way to talk yourself into a bad mapping.
#
# The verdict is advisory. It reasons from size, padding and known footers; it
# cannot know that two emulators agree on the meaning of every byte. Loading
# the bridged save in the destination emulator is still the real test.

set -u

# Unlike save-bridge.sh, this one needs python3. unraid's base OS does not ship
# it, so run this from the Deck or the desktop against the NFS/SMB mount rather
# than on the NAS itself.
command -v python3 >/dev/null || {
  echo "ERROR: python3 not found. Run this from a machine that has it (the Deck)," >&2
  echo "       pointing at the same files over the NFS or SMB mount." >&2
  exit 1
}

[ $# -ge 1 ] && [ $# -le 2 ] || { echo "usage: $0 <file> [file-b]" >&2; exit 1; }
for f in "$@"; do [ -r "$f" ] || { echo "ERROR: cannot read: $f" >&2; exit 1; }; done

python3 - "$@" <<'PY'
import sys, os

# Raw battery saves are always a power of two: GB/GBC SRAM 512B-128K,
# GBA EEPROM 512B/8K, GBA SRAM 32K, GBA Flash 64K/128K, SNES 2K-128K.
POW2 = {512, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144}

FOOTERS = [
    (b"|-DESMUME SAVE-|", "DeSmuME .dsv footer -- NOT raw, needs stripping"),
    (b"# DeSmuME", "DeSmuME marker"),
]

def classify(path):
    b = open(path, "rb").read()
    n = len(b)
    ff, zero = b.count(0xFF), b.count(0)
    other = n - ff - zero
    r = {"path": path, "size": n, "ff": ff, "zero": zero, "other": other,
         "empty": ff == n, "notes": [], "raw": True}

    for magic, why in FOOTERS:
        if magic in b:
            r["notes"].append(why); r["raw"] = False

    if n in POW2:
        r["notes"].append("size is a clean power of two -- consistent with a raw dump")
    else:
        # A raw dump plus a small appendix (RTC state, metadata) lands just
        # above a power of two. That tail is exactly what a rename would
        # mishandle when the other emulator does not expect it.
        base = max((p for p in POW2 if p < n), default=0)
        tail = n - base
        if base and tail <= 4096:
            r["notes"].append(
                f"size is {base} + {tail} trailing bytes -- likely an RTC or metadata "
                f"appendix, which the other emulator may not expect")
            r["raw"] = False
        else:
            r["notes"].append("size is not a standard save size -- treat as unknown")
            r["raw"] = False

    if r["empty"]:
        r["notes"].append("ENTIRELY 0xFF -- this is a blank save with no progress")
    return r

def show(r):
    print(f"  {os.path.basename(r['path'])[:64]}")
    print(f"    size   {r['size']} bytes")
    print(f"    bytes  0xFF {100*r['ff']/r['size']:.1f}%   "
          f"0x00 {100*r['zero']/r['size']:.1f}%   other {100*r['other']/r['size']:.1f}%")
    for n in r["notes"]:
        print(f"    - {n}")

files = sys.argv[1:]
results = [classify(f) for f in files]
print()
for r in results:
    show(r); print()

if len(results) == 1:
    r = results[0]
    print("VERDICT:", "raw dump -- a rename is plausible, confirm against the other emulator"
          if r["raw"] and not r["empty"] else
          "not a plain raw dump, or empty -- do not map on this evidence alone")
    sys.exit(0 if r["raw"] and not r["empty"] else 1)

a, b = results
print("=" * 68)
if a["empty"] or b["empty"]:
    print("VERDICT: REJECTED -- at least one save is blank (100% 0xFF).")
    print("         Two empty saves match trivially and prove nothing.")
    print("         Play each game far enough to save real progress, then re-run.")
    sys.exit(1)
if a["size"] != b["size"]:
    print(f"VERDICT: NOT SAFE -- sizes differ ({a['size']} vs {b['size']}).")
    print("         Different save types or a footer on one side. Needs conversion.")
    sys.exit(1)
if not (a["raw"] and b["raw"]):
    print("VERDICT: NOT SAFE -- at least one side is not a plain raw dump.")
    sys.exit(1)

# Same size, both raw, both populated. Compare structure at 4K granularity:
# two saves of one game should agree on WHICH regions are used even when the
# progress differs.
ba, bb = open(a["path"], "rb").read(), open(b["path"], "rb").read()
sec = lambda x: "".join("F" if set(x[i*4096:(i+1)*4096]) == {0xFF} else "D"
                        for i in range(len(x)//4096))
sa, sb = sec(ba), sec(bb)
print(f"  4K region map A: {sa}")
print(f"  4K region map B: {sb}")
print()
print("VERDICT: RENAME LIKELY SAFE -- same size, both raw dumps, no footers.")
if sa != sb:
    print("         (region maps differ, which is normal for different playthroughs")
    print("          or a different number of saves -- not a problem by itself)")
print("         Confirm by loading the bridged save in the destination emulator")
print("         before adding the row to MAPPINGS.")
PY
