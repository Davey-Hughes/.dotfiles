#!/bin/bash
#
# Collects every emulator's save data into one directory that Syncthing can
# read, so the whole lot travels as a single folder instead of one per
# emulator.
#
# WHY THE DATA HAS TO MOVE AT ALL
#
# Flatpak refuses one flatpak access to another's ~/.var/app, and granting
# --filesystem=host does not lift it -- the exclusion is hardcoded. A Syncthing
# folder rooted at a flatpak emulator's own directory scans clean, reports
# "Completed initial scan", and indexes zero files with no error, which is a
# genuinely hard failure to diagnose. Anything under ~/.var/app is therefore
# unsyncable in place.
#
# WHY THE SYMLINKS POINT INWARD, NOT OUTWARD
#
# EmuDeck already links Emulation/saves/<emu> out to each emulator's real
# directory, which looks like the same idea but is useless here: Syncthing
# stores a symlink as a symlink and never follows it, so those links replicate
# to other machines as dangling absolute paths. The bytes have to sit inside
# the synced folder, with the emulator's expected path pointing in.
#
# Mechanism per emulator, preferring configuration over symlinks wherever the
# emulator exposes a setting, since a setting survives the app recreating its
# directory tree:
#
#   retroarch    config   11 path settings in retroarch.cfg
#   duckstation  config   Directory / SaveStates in settings.ini
#   melonds      config   SaveFilePath / SavestatePath in melonDS.ini
#   everything else       the save directory becomes a symlink inward
#
# RUN THIS AFTER EmuDeck and the emulators are installed, with all of them
# closed. EmuDeck is not managed by these scripts, so on a fresh Deck this is
# a no-op until they exist.
#
# Idempotent, and it will not overwrite synced data with a freshly-installed
# emulator's empty defaults.

set -u

SYNC_DIR="$HOME/.local/share/emulator-sync"
VAR="$HOME/.var/app"

# Refuse to move directories out from under a running emulator.
RUNNING=""
for p in retroarch duckstation-qt duckstation PPSSPPSDL PPSSPPQt dolphin-emu \
         melonDS scummvm primehack flycast shadps4 Ryujinx eden; do
  pgrep -x "$p" >/dev/null 2>&1 && RUNNING="$RUNNING $p"
done
if [ -n "$RUNNING" ]; then
  echo "ERROR: close these first, they are running:$RUNNING" >&2
  exit 1
fi

mkdir -p "$SYNC_DIR"

# adopt <real_path> <sync_subpath>
#
# Makes $SYNC_DIR/<sync_subpath> the real storage and leaves <real_path> as a
# symlink to it. Existing content is merged in with cp -an, never overwritten:
# on a re-imaged Deck, Syncthing restores the synced copy while a fresh
# emulator lays down empty defaults, and letting those defaults win would be
# the worst available outcome.
adopt() {
  local real="$1" dest="$SYNC_DIR/$2"

  if [ -L "$real" ] && [ "$(readlink -f "$real")" = "$dest" ]; then
    return 0                                    # already adopted
  fi

  mkdir -p "$dest"

  if [ -d "$real" ] && [ ! -L "$real" ]; then
    local n
    n=$(find "$real" -type f -not -path '*/.stversions/*' -not -path '*/.stfolder/*' \
          -not -name '.stignore' 2>/dev/null | wc -l)
    # Syncthing's own bookkeeping must not come along. A folder root carries
    # .stversions, .stfolder and .stignore; copied in, they become ordinary
    # files inside the new folder and replicate to every device -- version
    # history masquerading as live data. Import history separately if wanted.
    ( cd "$real" && find . -mindepth 1 -maxdepth 1 \
        -not -name '.stversions' -not -name '.stfolder' -not -name '.stignore' \
        -exec cp -an {} "$dest/" \; ) 2>/dev/null
    rm -rf "${real:?}"
    [ "$n" -gt 0 ] && echo "    migrated $n file(s) from $real"
  else
    rm -f "$real"                               # stale or wrong symlink
  fi

  mkdir -p "$(dirname "$real")"
  ln -s "$dest" "$real"
  echo "    linked $real"
}

# set_kv <file> <key> <value> [separator]
set_kv() {
  local file="$1" key="$2" val="$3" sep="${4:- = }" cur
  [ -f "$file" ] || return 0
  cur=$(sed -n "s|^$key${sep}\(.*\)$|\1|p" "$file" | head -1)
  [ "$cur" = "$val" ] && return 0
  if grep -q "^$key${sep}" "$file"; then
    sed -i "s|^$key${sep}.*|$key${sep}$val|" "$file"
  else
    printf '%s%s%s\n' "$key" "$sep" "$val" >> "$file"
  fi
  echo "    set $key"
}

# --- retroarch (config-driven) ----------------------------------------------

RA_DIR="$VAR/org.libretro.RetroArch/config/retroarch"
RA_CFG="$RA_DIR/retroarch.cfg"
if [ -d "$RA_DIR" ]; then
  echo "retroarch:"
  mkdir -p "$SYNC_DIR"/retroarch/{saves,states,config,playlists,screenshots}

  # Fold in the previous single-emulator layout, then the flatpak's own dirs.
  for src in "$HOME/.local/share/retroarch-sync" "$RA_DIR"; do
    for d in saves states config playlists screenshots; do
      [ -d "$src/$d" ] || continue
      before=$(find "$SYNC_DIR/retroarch/$d" -type f 2>/dev/null | wc -l)
      ( cd "$src/$d" && find . -mindepth 1 -maxdepth 1 \
          -not -name '.stversions' -not -name '.stfolder' -not -name '.stignore' \
          -exec cp -an {} "$SYNC_DIR/retroarch/$d/" \; ) 2>/dev/null
      after=$(find "$SYNC_DIR/retroarch/$d" -type f 2>/dev/null | wc -l)
      [ "$after" -gt "$before" ] && echo "    seeded $d: +$((after - before)) file(s)"
    done
  done

  if [ -f "$RA_CFG" ]; then
    # RetroArch normalises absolute home paths to "~/" whenever it rewrites the
    # config, so both forms must count as already-correct or every run rewrites
    # settings that were fine.
    set_ra() {
      local key="$1" val="$2" cur
      cur=$(sed -n "s/^$key = \"\(.*\)\"$/\1/p" "$RA_CFG" | head -1)
      [ "$cur" = "$val" ] && return 0
      [ "$cur" = "${val/#$HOME/\~}" ] && return 0
      if grep -q "^$key = " "$RA_CFG"; then
        sed -i "s|^$key = .*|$key = \"$val\"|" "$RA_CFG"
      else
        printf '%s = "%s"\n' "$key" "$val" >> "$RA_CFG"
      fi
      echo "    set $key"
    }
    R="$SYNC_DIR/retroarch"
    set_ra savefile_directory        "$R/saves"
    set_ra savestate_directory       "$R/states"
    set_ra rgui_config_directory     "$R/config"
    set_ra input_remapping_directory "$R/config/remaps"
    set_ra playlist_directory        "$R/playlists"
    set_ra screenshot_directory      "$R/screenshots"
    # Absolute FILE paths inside playlists/builtin. Moving playlist_directory
    # alone leaves RetroArch writing history back to the old location, silently
    # unsynced -- noticed weeks later when playtime stops accumulating.
    for p in favorites history image_history music_history video_history; do
      set_ra "content_${p}_path" "$R/playlists/builtin/content_${p}.lpl"
    done
    # Namespace saves by core, or every .srm lands in one directory keyed only
    # on ROM filename and the same game on two systems collides. Sorting by
    # content directory is the obvious alternative and is wrong for this
    # library, which nests deeply enough that "Rom Hacks" would become one
    # bucket shared by gb, gba and n64.
    set_ra sort_savefiles_enable  "true"
    set_ra sort_savestates_enable "true"
  else
    echo "    no retroarch.cfg yet -- launch it once, then re-run"
  fi
fi

# --- duckstation and melonDS (config-driven) --------------------------------

DS_INI="$HOME/.local/share/duckstation/settings.ini"
if [ -f "$DS_INI" ]; then
  echo "duckstation:"
  mkdir -p "$SYNC_DIR"/duckstation/{saves,states}
  for pair in "Directory:saves" "SaveStates:states"; do
    key="${pair%%:*}"; sub="${pair##*:}"
    old=$(sed -n "s|^$key = ||p" "$DS_INI" | head -1)
    [ -n "$old" ] && [ -d "$old" ] && cp -an "$old/." "$SYNC_DIR/duckstation/$sub/" 2>/dev/null
    set_kv "$DS_INI" "$key" "$SYNC_DIR/duckstation/$sub"
  done
fi

MD_INI="$VAR/net.kuribo64.melonDS/config/melonDS/melonDS.ini"
if [ -f "$MD_INI" ]; then
  echo "melonds:"
  mkdir -p "$SYNC_DIR"/melonds/{saves,states}
  for pair in "SaveFilePath:saves" "SavestatePath:states"; do
    key="${pair%%:*}"; sub="${pair##*:}"
    old=$(sed -n "s|^$key=||p" "$MD_INI" | head -1)
    [ -n "$old" ] && [ -d "$old" ] && cp -an "$old/." "$SYNC_DIR/melonds/$sub/" 2>/dev/null
    set_kv "$MD_INI" "$key" "$SYNC_DIR/melonds/$sub" "="
  done
fi

# --- symlinked emulators ----------------------------------------------------
#
# All six flatpaks here already carry --filesystem=host (EmuDeck granted it),
# so they can follow a symlink out of their sandbox. Check with
# `flatpak override --user --show <id>` if one ever stops saving.

echo "symlinked:"
[ -d "$VAR/org.ppsspp.PPSSPP" ] && {
  adopt "$VAR/org.ppsspp.PPSSPP/config/ppsspp/PSP/SAVEDATA"     ppsspp/SAVEDATA
  adopt "$VAR/org.ppsspp.PPSSPP/config/ppsspp/PSP/PPSSPP_STATE" ppsspp/PPSSPP_STATE; }

for pair in "org.DolphinEmu.dolphin-emu:dolphin" "io.github.shiiion.primehack:primehack"; do
  id="${pair%%:*}"; name="${pair##*:}"
  [ -d "$VAR/$id" ] || continue
  for d in GC Wii StateSaves; do adopt "$VAR/$id/data/dolphin-emu/$d" "$name/$d"; done
done

[ -d "$VAR/org.scummvm.ScummVM" ] && adopt "$VAR/org.scummvm.ScummVM/data/scummvm/saves" scummvm/saves
[ -d "$VAR/org.flycast.Flycast" ] && {
  adopt "$VAR/org.flycast.Flycast/data/flycast"        flycast/saves
  adopt "$VAR/org.flycast.Flycast/config/data/flycast" flycast/states; }

# Native emulators. These were always syncable, but folding them in collapses
# three more Syncthing folders into this one. Only save data moves -- Ryujinx
# firmware (bis/system/Contents, 623 MB) and its sdcard tree stay put, which is
# also why the old per-folder .stignore stops being needed.
[ -d "$HOME/.local/share/shadPS4/home" ] && adopt "$HOME/.local/share/shadPS4/home" shadps4

RYU="$HOME/.config/Ryujinx"
if [ -d "$RYU" ]; then
  adopt "$RYU/bis/user/save"     ryujinx/save
  adopt "$RYU/bis/user/saveMeta" ryujinx/saveMeta
  adopt "$RYU/bis/system/save"   ryujinx/system-save
fi

# Eden nests saves under a per-profile UUID that is regenerated if the profile
# is, so the directory is found rather than assumed.
EDEN_SAVE=$(find "$HOME/.local/share/eden/nand/user/save/0000000000000000" \
              -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
[ -n "$EDEN_SAVE" ] && adopt "$EDEN_SAVE" eden

echo
echo "Emulator save data unified at $SYNC_DIR"
echo "Point one Syncthing folder at it; the per-emulator folders can be removed."
