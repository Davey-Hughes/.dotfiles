# Emulator save syncing

Every emulator's save data lives in one Syncthing folder,
`~/.local/share/emulator-sync`, shared between the Deck, Davey-Endeavor and
DaveyNet. `os/steamdeck/emulator-saves.sh` builds and repairs that arrangement;
`os/unraid/save-bridge.sh` bridges it to the iPhone and to the MiSTer FPGA.

This file records the things that were expensive to find out, and would be
expensive to find out again.

## The save bridge has three participants, not two

A *participant* is one place saves live. Each arrives on the NAS as its own
Syncthing folder, and the bridge reconciles them every ten minutes:

| Participant | Folder root on the NAS | What it is |
|---|---|---|
| `manic` | `/mnt/user/games/Syncthing/ManicEMU` | Manic EMU on the iPhone, via Synctrain |
| `retroarch` | `/mnt/user/games/Syncthing/Emulator Saves/retroarch` | one folder, five emulators, RetroArch a subdirectory of it |
| `mister` | `/mnt/user/games/Syncthing/MiSTer/saves` | the MiSTer FPGA; moved off the mirrors share on 2026-08-21 |

Note the space in `Emulator Saves`. Every expansion of that path in every script
is quoted, and has to stay that way.

**The MiSTer's folder must be Send & Receive**, and `save-bridge-cron.sh`
refuses to run until it has confirmed that. Writing into a `receiveonly` folder
does not fail: Syncthing accepts the file, files it under Local Additions and
never sends it, so the console never sees the save. The documented cure for
local additions — Revert Local Changes — then restores the MiSTer's *older*
bytes over the bridge's, and the next run reads the MiSTer as the one that
moved and fans that stale save over everybody else. Exit 0, no warning. The
idle check cannot catch it either: a `receiveonly` folder full of local
additions still reports state `idle`.

`save-bridge.sh` exits **0** when nothing went wrong, **1** when at least one
copy failed, and **2** when at least one game was left alone because the
evidence was ambiguous. 2 outranks 1: a conflict is a decision a human has to
make, whereas a failed copy can simply be retried. Dry run is the default —
only `--apply` writes anything.

With three participants the usual conflict is no longer "both sides changed".
It is a participant with no record of its own sitting beside participants that
agree with theirs, which means *nothing moved*: something was newly added and is
holding an old save. The fix is to make the live tree agree, not to delete
anything out of the conflict stash — the bridge never reads that stash again.

## Flatpak hides `~/.var/app` from other flatpaks

Granting `--filesystem=host` does **not** lift it. The exclusion is hardcoded,
so a flatpak-packaged Syncthing can never read a flatpak emulator's data
directory, and the failure is almost silent:

    Adding folder (folder.label="RetroArch Saves" ...)
    Ready to synchronize
    Completed initial scan

Zero files indexed, no error. `/rest/db/ignores` returns `null` because the
`.stignore` is unreadable too. Nothing anywhere says "permission denied".

Confirmed directly — SyncThingy reads `~/.config/Ryujinx` fine and gets "No
such file or directory" for `~/.var/app/org.libretro.RetroArch/...`, despite
carrying `filesystems=host`.

Consequence: any flatpak emulator's saves have to move out of `~/.var/app`
before they can sync. Native emulators (Eden, Ryujinx, shadPS4) were never
affected.

## Syncthing stores symlinks, it does not follow them

EmuDeck links `Emulation/saves/<emu>` **out** to each emulator's real
directory. That looks like the abstraction we want and is useless for syncing:
Syncthing replicates the link itself, so those absolute paths arrive on other
machines as dangling links.

The bytes have to sit *inside* the synced folder with the emulator's expected
path pointing **in** — the opposite direction to EmuDeck's.

## A bare directory ignore pattern expands to include `/**`

In a whitelist-style `.stignore`, un-ignoring an ancestor to allow descent also
re-includes everything beneath it:

    !/bis        expands to    !/bis  AND  !/bis/**

That silently pulled a 623 MB firmware tree back into a folder meant to hold
28 MB of saves. First match wins, so exclusions must precede the ancestors:

    !/bis/user/save/**
    !/bis/system/save/**
    /bis/system/Contents      <- must come before the lines below
    /bis/user/Contents
    !/bis/user
    !/bis/system
    !/bis
    *

Deep selections are safe; ancestor selections are the trap. Always check the
`expanded` array from `GET /rest/db/ignores?folder=<id>` — it is the only
reliable view of what Syncthing did with the patterns.

## `.stversions` is per-device and never synced

The same folder held 40K of history on the Deck and 371M on the NAS. Version
history therefore has to be migrated by hand, and it must not be swept into a
new folder by a naive `cp -a` of a folder root — copied in, `.stversions`,
`.stfolder` and `.stignore` stop being bookkeeping and become ordinary files
that replicate everywhere.

Version history for a path the folder does not track is inert: Syncthing has no
live file to attach it to and can never offer it for restore.

## Manic EMU and RetroArch GBA saves are byte-compatible

Verified on a real 128K save from both emulators: identical size, no header, no
footer, and the same 14 / 14 / 2 / 2 sector layout (save slot A, slot B, Hall
of Fame, mystery gift), differing only in which slot was populated. Bridging
them is a rename, `.sav` <-> `.srm`, and nothing more.

**Matching size proves nothing on its own.** Two files of exactly 131,072 bytes
turned up during this work where one was 100% `0xFF` — a blank save. Two empty
saves agree perfectly and would "verify" any mapping at all, which is why
`os/unraid/save-format-check.sh` rejects them outright.

Not every system is like this.

## What each system has actually been verified against

"Bridgeable" is never a property of a system in the abstract. It is a claim
about two *named participants* and a real save from each, established by
`os/unraid/save-format-check.sh`. A row in MAPPINGS is exactly that claim.

| System | RetroArch | Manic EMU | MiSTer | In MAPPINGS |
|---|---|---|---|---|
| GBA | raw `.srm` | raw `.sav` | not checked | yes — `manic` + `retroarch` |
| SNES | raw `.srm` | `.srm`, unchecked | raw `.sav` | yes — `mister` + `retroarch` |
| GB / GBC | raw `.srm`, RTC in a separate `.rtc` | raw dump | raw dump, in three directories | no — two blockers, below |
| NES | raw 8192-byte `.srm` (FCEUmm) | raw dump | three formats under one extension | yes — `mister` + `retroarch`, **8192 bytes only** |
| NDS | raw `.srm` | `.dsv` | — | no — DeSmuME appends a footer |
| N64 | 296,960-byte composite | separate files | — | no — needs splitting |

RetroArch packs N64 EEPROM + SRAM + FlashRAM + four mempaks into a single
`.srm` that nothing else reads.

**SNES verified 2026-08-21.** Three games are present on both the MiSTer and in
RetroArch's `Snes9x` directory under identical names — `ALttP-msu-Deluxe`,
`Retroid` and `The Legend of Zelda SNES`. All six files are 8192 bytes, all
classify as raw dumps with no footer, and all three pairs return RENAME LIKELY
SAFE. That is what put the `snes` row in MAPPINGS. Manic EMU's SNES saves are
already `.srm`, so adding the phone to that row is likely a plain copy rather
than a rename — but "likely" is not a verification, and it is not in the table.

**NES verified 2026-08-22 — but only at one size.** This replaces the earlier
finding that NES could not be verified at all for want of a RetroArch pair.
RetroArch does hold one NES save: FCEUmm's `Legend of Zelda, The (USA) (Rev
1).srm`, 8192 bytes. There is still no `saves/Mesen` directory — FCEUmm is the
core actually in use here. Checked against the MiSTer's `Zelda no Densetsu 1 -
The Hyrule Fantasy (Japan).sav`, also 8192 bytes, that pair returns RENAME
LIKELY SAFE.

That verifies **one size and nothing else**, and the MiSTer's nine NES saves are
three different formats sharing the `.sav` extension:

| Size | What it actually is |
|---|---|
| 8,192 | raw SRAM — byte-compatible with FCEUmm's `.srm`, and the only size with a verified pair |
| 32,768 | 8 KB of real save data followed by 24 KB of inert padding — the mapper's declared PRG-RAM size, not data. Six of the nine are this. |
| 131,072 | **not SRAM at all** — Famicom Disk System writable *disk images* (`Zelda no Densetsu`, `Metroid (Japan)`), with real data well past the 8 KB mark |

A MAPPINGS row applies to every file in a directory, so the `nes` row cannot
exclude the other two by name — they sit in the same directory under the same
extension. It excludes them by size instead:

    "nes|sizes=8192|mister:NES:sav|retroarch:saves/FCEUmm:srm"

Widening that list means verifying the wider size against a real pair first. A
32,768-byte file renamed to `.srm` hands FCEUmm four times the length it writes,
and nothing here establishes what it does with one; a 131,072-byte file hands it
a disk image as battery backup.

### The two size guards

Both live in `os/unraid/save-bridge.sh` and both run *before* the
agreed/changed/unknown decision, which is the whole point — differing sizes
would otherwise arrive at that decision as an ordinary hash disagreement, get
resolved by picking a "winner", and fan an FDS disk image over somebody's SRAM
on exit 0.

- **Always on.** If two or more participants present hold files of *different*
  sizes, the game is skipped. A rename cannot be correct when the lengths
  differ, so this is never a conflict to settle by picking a side — it is a
  format incompatibility, and `--seed` does not override it either.
- **Optional, per row.** `sizes=N[,N...]` is a field in the MAPPINGS row —
  a constraint, not a participant, and it may sit anywhere after the system
  name. A game whose file is not one of those lengths on any participant is
  skipped for that row. A row without one accepts any length, exactly as
  before. A malformed value (`sizes=8k`, `sizes=`, a trailing comma, two
  `sizes=` fields on one row) is rejected at preflight with exit 1, like an
  undeclared participant — a constraint nobody can satisfy would otherwise
  disable the row silently, and read exactly like empty directories.

Both fire on **permanent, expected** conditions, so both log *indented* and
count as skipped, leaving the exit status alone. That is deliberate:
`save-bridge-cron.sh` raises an unraid notification for any line matching
`^WARNING:` and runs every ten minutes, so an unindented line here would be a
push notification every ten minutes forever — an FDS disk image will never
become bridgeable. `^WARNING:` stays reserved for structural problems such as a
missing participant folder.

**GB/GBC is blocked on two independent grounds**, either of which is enough on
its own:

1. **RTC.** `Pokemon - Crystal Version (USA).sav` on the MiSTer is 33,280 bytes
   = 32,768 + 512. That 512-byte tail is a real-time-clock appendix, and
   Gambatte keeps RTC state in a *separate* `.rtc` file rather than appended to
   the SRAM — so a rename hands it 512 bytes it does not expect. Gold, Silver
   and Crystal are all affected, and they are the GB games that matter here.

   The general rule: a save that is a clean power of two has no appendix; one
   that is a power of two plus a small tail does. `gb` has to be verified with
   an RTC game, or the check passes while the mapping stays wrong for exactly
   the games it was written for.

2. **Cardinality.** The MiSTer keeps *three* Game Boy save directories —
   `GAMEBOY`, `GBC` and `SGB` — against RetroArch's single `Gambatte`, and the
   same game appears in two of them at once (Link's Awakening DX in `GBC` and
   `SGB`; Pokemon Yellow in `GAMEBOY` and `GBC`). Which one is authoritative is
   not answerable from the filesystem.

   The shortcut that suggests itself — naming `mister` twice on one MAPPINGS row
   — is refused by the bridge's preflight, and deliberately. Everything the
   bridge records per game is keyed on the participant *name*, so a second spec
   for a name already on the row silently replaces the first: one directory is
   never read at all, two copies that disagree are reported as agreeing, and
   both specs write their backup to the same path. Three directories against one
   `Gambatte` need three participants and three rows, or a decision about which
   directory wins.

## Sorting RetroArch saves by core, not by content directory

Flat, every `.srm` is keyed only on ROM filename, so the same game on two
systems collides. `sort_savefiles_by_content_enable` looks like the fix and is
wrong here — this ROM library nests deeply, so the content directory is
`Rom Hacks` for gb, gba and n64 alike, merging three systems into one bucket.
`sort_savefiles_enable` (by core name) is immune to nesting.

## Order of operations that will destroy data if reversed

- **Ignoring, then deleting.** Adding an ignore never deletes anything. But
  deleting files that are still synced propagates everywhere. Ignore first,
  confirm idle, delete second.
- **Migrating out of a live Syncthing folder.** Moving data out of a folder
  root registers as deletion and propagates. Remove the folder definition on
  every device first.
- **`emulationPath` is not a normally-quoted value.** EmuDeck writes
  `emulationPath="/run/media/deck/SD1TB"/Emulation`, with the quotes wrapping
  only part of it. Unquoting truncates at the closing quote and silently yields
  a path that looks like an unmounted SD card.

## unraid specifics

The base OS ships neither `python3` nor `jq`; JSON has to be parsed with
`grep`/`sed`/`awk`. Syncthing runs in a container, so it reports container
paths while a host script knows host paths and the two never compare equal —
match on the last path component instead. The Syncthing GUI is HTTPS with a
self-signed certificate, so `curl` needs `-k` and an `https://` URL, or a 307
comes back that `-f` does not treat as an error.
