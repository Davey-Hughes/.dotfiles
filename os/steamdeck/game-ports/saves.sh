#!/bin/bash
#
# Collects the native ports' save data into one directory for Syncthing to
# carry, the way emulator-saves.sh does for the emulators. Separate folder,
# separate script, because these are not emulators and the shape of the
# problem is different -- see below.
#
# WHY THE WHOLE DATA DIRECTORY AND NOT A saves/ SUBDIRECTORY
#
# The five ports keep saves in three incompatible shapes:
#
#   2 Ship, Lighthouse   a saves/ directory
#   Ship of Harkinian    oot_save.sav and global.sav loose at the data root
#   SpaghettiKart        default.sav loose at the data root
#   Dusklight            .gci memory-card files under its own XDG directory
#
# Two of those have no directory to adopt, and symlinking an individual save
# FILE is a trap: an app that writes a temp file and renames it over the
# target replaces the symlink with a regular file, and the save silently stops
# syncing from then on.
#
# So the whole data directory moves into the sync folder and the ignore list
# below subtracts what must not travel. That inverts the usual risk: instead
# of a new save file being missed because nobody updated a list of paths, a
# new NON-save file is carried until someone adds it to .stignore. The failure
# mode of the first is lost progress; of the second, wasted bytes.
#
# WHY THE SYMLINKS POINT INWARD
#
# Syncthing stores a symlink as a symlink and never follows it, so the bytes
# have to sit inside the synced folder with the app's expected path pointing
# in -- not the other way round. Same reasoning as emulator-saves.sh.
#
# RUN THIS with all five ports closed. Idempotent, and it will not overwrite
# synced data with a freshly-installed port's empty defaults.

set -u

SYNC_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/recomp-sync"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
OLD_EMU_SYNC="${XDG_DATA_HOME:-$HOME/.local/share}/emulator-sync"
PORTS_CONF="$(dirname "$0")/ports.conf"

if [ ! -r "$PORTS_CONF" ]; then
  echo "ERROR: cannot read $PORTS_CONF" >&2
  exit 1
fi

# Refuse to move directories out from under a running port.
RUNNING=""
for p in soh.elf 2s2h.elf lighthouse.elf spaghetti.elf dusklight; do
  pgrep -x "$p" >/dev/null 2>&1 && RUNNING="$RUNNING $p"
done
if [ -n "$RUNNING" ]; then
  echo "ERROR: close these first, they are running:$RUNNING" >&2
  exit 1
fi

mkdir -p "$SYNC_DIR"

# --- what must not travel ----------------------------------------------------
#
# Written every run, so the reasoning stays with the list. A bare directory
# name in .stignore also covers its contents, so `logs` is enough for `logs/**`
# -- see docs/emulator-save-sync.md on that expansion.
#
#   game dumps      yours to supply per machine, and 34-935 MB each
#   *.o2r / *.otr   rebuilt from the dump, and tied to the port's version, so
#                   a synced one is wrong the moment two machines differ
#   config + imgui  almost entirely controller bindings and window layout,
#                   which are per-device by nature
#   gamecontrollerdb shipped with the port, restored by install.sh
#   logs / sentry   noise; sentry is Dusklight's crash reporter
#   mods            large, and installed per machine on purpose
cat > "$SYNC_DIR/.stignore" <<'IGNORE'
// Written by os/steamdeck/game-ports/saves.sh -- edit there, not here.
// Game dumps: supplied per machine, never replicated.
*.z64
*.n64
*.v64
*.rvz
*.iso
*.gcm
*.ciso
*.gcz
*.nfs
*.wbfs
*.wia
*.tgc
// Derived from the dump by the port itself, and version-specific.
*.o2r
*.otr
// Per-device: controller bindings, window layout, backend choice.
*.cfg.json
config.json
shipofharkinian.json
2ship2harkinian.json
imgui.ini
gamecontrollerdb.txt
// Noise.
logs
sentry
crashes
// Installed per machine.
mods
IGNORE
echo "wrote $SYNC_DIR/.stignore"

# adopt <real_path> <sync_subpath>
#
# Makes $SYNC_DIR/<sync_subpath> the real storage and leaves <real_path> as a
# symlink to it. Existing content is merged with cp -an, never overwritten: on
# a re-imaged Deck, Syncthing restores the synced copy while a fresh install
# lays down empty defaults, and letting those defaults win would be the worst
# available outcome.
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
    # Syncthing's own bookkeeping must not come along: copied in, .stversions
    # and .stfolder become ordinary files inside the new folder and replicate
    # everywhere as version history masquerading as live data.
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

# --- fold in the previous arrangement ----------------------------------------
#
# 2 Ship's saves were adopted into the Emulator Saves folder before these
# ports had one of their own. Anything already there is pulled across first,
# so the move does not strand a save on the other side.
if [ -d "$OLD_EMU_SYNC/2ship" ]; then
  moved=$(find "$OLD_EMU_SYNC/2ship" -type f -not -name '.stignore' 2>/dev/null | wc -l)
  mkdir -p "$SYNC_DIR/2ship"
  ( cd "$OLD_EMU_SYNC/2ship" && find . -mindepth 1 -maxdepth 1 \
      -not -name '.stversions' -not -name '.stfolder' -not -name '.stignore' \
      -exec cp -an {} "$SYNC_DIR/2ship/" \; ) 2>/dev/null
  echo "folded in $moved file(s) from the old Emulator Saves location"
  rm -rf "${OLD_EMU_SYNC:?}/2ship"
fi

# A saves/ that still points into the old folder has to go before its parent
# is adopted, or the link travels into the new folder still aimed at the old.
for stale in "$DATA_ROOT"/*/saves; do
  [ -L "$stale" ] || continue
  case "$(readlink -f "$stale")" in
    "$OLD_EMU_SYNC"/*) rm -f "$stale"; echo "removed stale link $stale" ;;
  esac
done

# --- adopt each port's data directory ----------------------------------------
echo "ports:"
while IFS='|' read -r key name repo pattern sub datastyle assetstyle dump; do
  case "$key" in ''|'#'*) continue ;; esac
  : "$repo" "$pattern" "$datastyle" "$assetstyle" "$dump"
  [ -d "$DATA_ROOT/$sub" ] || [ -L "$DATA_ROOT/$sub" ] || continue
  echo "  $name"
  adopt "$DATA_ROOT/$sub" "$key"
done <<<"$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$PORTS_CONF")"

echo
echo "Port save data unified at $SYNC_DIR"
echo "Point a Syncthing folder at it -- suggested label: Recomp Saves"
