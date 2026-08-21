# Emulator save syncing

Every emulator's save data lives in one Syncthing folder,
`~/.local/share/emulator-sync`, shared between the Deck, Davey-Endeavor and
DaveyNet. `os/steamdeck/emulator-saves.sh` builds and repairs that arrangement;
`os/unraid/save-bridge.sh` bridges it to the iPhone.

This file records the things that were expensive to find out, and would be
expensive to find out again.

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

Not every system is like this:

| System | RetroArch | Manic EMU | Bridgeable |
|---|---|---|---|
| GBA, GB, SNES, NES | raw dump | raw dump | yes, rename only |
| NDS | raw `.srm` | `.dsv` | no — DeSmuME appends a footer |
| N64 | 296,960-byte composite | separate files | no — needs splitting |

RetroArch packs N64 EEPROM + SRAM + FlashRAM + four mempaks into a single
`.srm` that nothing else reads.

GB/GBC carries a specific hazard for this library: Pokémon Gold, Silver and
Crystal all use a real-time clock, and implementations disagree about whether
clock state is appended to the save file. A save that is a clean power of two
has no appendix; one that is a power of two plus a small tail does. Verify
`gb` with an RTC game, or the check passes while the mapping stays wrong for
exactly the games that matter.

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
