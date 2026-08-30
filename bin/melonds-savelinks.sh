#!/usr/bin/env bash
# Point standalone melonDS at RetroArch's melonDS DS .srm files.
#
# Both write the same raw NDS cart-save dump and differ only in extension
# (.sav vs .srm), and neither lets you change it -- so each .srm is exposed
# under a .sav name via a symlink.
#
# The link directory MUST live outside the emulator-sync Syncthing folder:
# Syncthing does not sync symlinks and reads the swap as a deletion.
#
# Re-run after adding ROMs. Idempotent. Called by os/steamdeck/emulator-saves.sh.
set -euo pipefail

SRM="$HOME/.local/share/emulator-sync/retroarch/saves/melonDS DS"
LINKS="$HOME/.local/share/melonds-savelinks"

# First existing candidate wins: Deck SD card, then the NFS library.
ROMS=""
for c in "/run/media/deck/SD1TB/Emulation/roms/nds" \
         "/mnt/daveynet/nfs/games/Favorite Roms/Nintendo/Nintendo DS"; do
  [ -d "$c" ] && { ROMS="$c"; break; }
done
[ -n "$ROMS" ] || { echo "no NDS ROM directory found (share not mounted?)" >&2; exit 1; }

mkdir -p "$LINKS" "$SRM"
n=0
for rom in "$ROMS"/*.zip "$ROMS"/*.nds; do
  [ -e "$rom" ] || continue
  base=$(basename "$rom"); base="${base%.*}"
  ln -sfn "$SRM/$base.srm" "$LINKS/$base.sav"
  n=$((n + 1))
done
echo "$n links in $LINKS  (roms: $ROMS)"
