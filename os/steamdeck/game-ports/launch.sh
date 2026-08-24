#!/bin/bash
#
# Launches one of the native ports. This is the path every Steam shortcut
# points at, with the port's key as its only argument:
#
#     launch.sh soh
#
# WHY THE SHORTCUTS DO NOT POINT AT THE APPIMAGES
#
# Two reasons, and the second is the one that loses save files.
#
# install.sh replaces each appimage in place on every update, so a shortcut
# aimed straight at one survives only by luck of the filename.
#
# More importantly, the libultraship ports read $SHIP_HOME to decide where
# config, saves, mods and the ROM live, and with it unset they do NOT fall
# back to the appimage's own directory as their readmes imply -- they use the
# current working directory. Steam sets that from each shortcut's StartDir, so
# without this wrapper a port's entire save directory becomes a property of
# how it happened to be launched: change StartDir, or run it from a terminal,
# and it finds no ROM, no config, and looks freshly installed.
#
# Keep this path stable. Steam stores it absolutely in shortcuts.vdf, so a
# rename or a move breaks every entry silently, in Game Mode, with no error --
# and fixing it means rewriting that file with Steam shut down, because Steam
# rewrites it from memory on exit. Moving this file once already cost exactly
# that. If it has to move again, plan on doing both halves together.

set -u

PORTS_CONF="$(dirname "$0")/ports.conf"

usage() {
  echo "usage: ${0##*/} <port>" >&2
  [ -r "$PORTS_CONF" ] &&
    echo "ports: $(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$PORTS_CONF" |
                   cut -d'|' -f1 | tr '\n' ' ')" >&2
  exit 2
}

[ $# -ge 1 ] || usage
KEY="$1"; shift

if [ ! -r "$PORTS_CONF" ]; then
  echo "ERROR: cannot read $PORTS_CONF" >&2
  exit 1
fi

ROW=$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$PORTS_CONF" |
      grep -m1 "^$KEY|") || true
[ -n "$ROW" ] || usage

IFS='|' read -r key name repo pattern sub datastyle assetstyle dump <<<"$ROW"
# repo, pattern, assetstyle and dump are the installer's columns, not ours.
: "$repo" "$pattern" "$assetstyle" "$dump"

APP="$HOME/Applications/$key/$key.appimage"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/$sub"

if [ ! -x "$APP" ]; then
  echo "ERROR: $name is not installed ($APP is missing)." >&2
  echo "       Run os/steamdeck/game-ports/install.sh $key" >&2
  exit 1
fi

mkdir -p "$DATA"

# Only the libultraship ports take SHIP_HOME. Dusklight picks its own XDG
# directory and exporting it at one would be a lie in the process environment
# -- harmless today, misleading the next time someone reads this.
if [ "$datastyle" = "shiphome" ]; then
  SHIP_HOME="$DATA"
  export SHIP_HOME
fi

# exec, so Steam tracks the game's own process rather than a wrapper shell
# that has already handed off -- otherwise the overlay and playtime attach to
# the wrong thing.
exec "$APP" "$@"
