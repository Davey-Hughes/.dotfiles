#!/usr/bin/env bash
#
# Copy this device's RetroArch config into its own subdirectory of the synced
# retroarch-configs folder.
#
# THIS FILE IS THE SOURCE OF TRUTH. It is stowed to ~/.local/bin and driven by
# retroarch-config-backup.path; the synced folder carries only the configs it
# produces, deliberately not a second copy of this script, because two copies
# of a backup script are two copies that can disagree about what they back up.
#
# This is a BACKUP, not a settings-sharing mechanism. Every device writes only
# to its own subdirectory and reads nobody else's, because retroarch.cfg is
# machine-specific: audio/input/menu drivers and core paths differ per device,
# and RetroArch rewrites all ~3400 settings on exit (config_save_on_exit).
# Sharing one file between devices would mean the last one to quit silently
# overwrites everybody else, with values that are wrong for their hardware.
#
# Deliberately NOT copied, because RetroArch re-downloads them in-app and they
# are ~1.5 GB: cores, assets, database, cheats, shaders, overlays, thumbnails,
# autoconfig. Saves, states, playlists and remaps are excluded too -- they are
# already synced by the separate emulator-sync folder.
#
# Restoring after a fresh install: copy retroarch.cfg back, then fix the paths
# that are specific to the new machine (the *_directory and *_path keys, and
# the driver keys) before launching. Do not restore another device's file.
set -uo pipefail

ROOT="${ROOT:-$HOME/.local/share/retroarch-configs}"
# Device name, and it MUST be unique across every device using this folder.
# Hostname alone is not enough: both Steam Decks report "steamdeck", and two
# devices sharing a subdirectory would overwrite each other's config -- the
# exact failure this per-device layout exists to prevent. Any device whose
# hostname is ambiguous writes its real name into the file below, which is
# deliberately NOT in the synced folder, since it describes this machine only.
NAME_FILE="${NAME_FILE:-$HOME/.config/retroarch-config-backup.device}"
if [ -z "${DEV:-}" ] && [ -r "$NAME_FILE" ]; then
  DEV="$(head -n1 "$NAME_FILE" | tr -d '"'"'[:space:]'"'"')"
fi
DEV="${DEV:-$(cat /etc/hostname 2>/dev/null || uname -n)}"
DEST="$ROOT/$DEV"

# RetroArch lives in a different place depending on how it was installed, and
# the Steam Deck's is a Flatpak. Take the first candidate that actually holds a
# retroarch.cfg rather than assuming; override with SRC= to force one.
if [ -z "${SRC:-}" ]; then
  for c in "$HOME/.var/app/org.libretro.RetroArch/config/retroarch" \
           "$HOME/.config/retroarch" \
           "$HOME/Library/Application Support/RetroArch"; do
    [ -f "$c/retroarch.cfg" ] && { SRC="$c"; break; }
  done
fi
[ -n "${SRC:-}" ] || { echo "found no retroarch.cfg in any known location" >&2; exit 1; }
[ -d "$SRC" ] || { echo "no RetroArch config dir at $SRC" >&2; exit 1; }
mkdir -p "$DEST"

changed=0
for f in retroarch.cfg retroarch_qt.cfg; do
  [ -f "$SRC/$f" ] || continue
  # Written to a temp file in the destination and renamed, so a reader (or
  # Syncthing's scanner) never sees a half-copied config.
  tmp="$DEST/.$f.tmp.$$"
  if ! cp -a "$SRC/$f" "$tmp"; then
    echo "failed to copy $f" >&2; rm -f "$tmp"; continue
  fi
  if [ -f "$DEST/$f" ] && cmp -s "$tmp" "$DEST/$f"; then
    rm -f "$tmp"; continue
  fi
  mv -f "$tmp" "$DEST/$f" && changed=$((changed + 1))
done

printf '%s  %s: %d file(s) updated from %s\n' "$(date -Is)" "$DEV" "$changed" "$SRC"
