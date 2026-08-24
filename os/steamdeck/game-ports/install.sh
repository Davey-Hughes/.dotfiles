#!/bin/bash
#
# Installs and updates the decompilation-based native ports: Ship of Harkinian,
# 2 Ship 2 Harkinian, Lighthouse, SpaghettiKart and Dusklight.
#
# WHY A SCRIPT AND NOT PACKAGES
#
# None of these is on flathub or in the AUR. Every one ships a Linux AppImage
# on its GitHub releases page and nothing else, and a pacman package would not
# survive the next SteamOS re-image regardless. The five together are under
# 110 MiB in ~/Applications, which costs the 5 GiB rootfs nothing.
#
# WHY ONE TABLE AND NOT FIVE SCRIPTS
#
# They differ in four ways and agree on everything else: the repo, whether the
# release asset is a zip or a bare AppImage, where the app keeps its mutable
# data, and which game dump it wants. Those are four columns in
# ports.conf. Anything a sixth port needs beyond a row there is a real
# difference worth writing code for; nothing so far has been.
#
# WHY THE GAME DATA IS NOT NEXT TO THE APPIMAGE
#
# The four Harbour Masters ports are built on libultraship, which reads
# $SHIP_HOME and honours it for config, saves, mods and the ROM. Left unset it
# does NOT fall back to the appimage's directory as the readmes imply -- it
# uses the current working directory, so the save location becomes a property
# of however the app was launched. Verified by running one with SHIP_HOME
# unset from a scratch directory and watching its config land there.
#
# Dusklight is not a Harbour Masters project and ignores SHIP_HOME entirely,
# using ~/.local/share/TwilitRealm/Dusklight on its own. That is already
# stable and outside the appimage, so it needs no wrapper -- hence the
# datastyle column rather than a blanket assumption.
#
# THE GAME DUMPS ARE YOURS TO SUPPLY
#
# None of these ships copyrighted assets; each builds its own archive from a
# dump you provide on first run. This script never fetches, moves or reads
# one. It only reports which are missing, and points at the upstream hash list
# rather than copying it here, because a stale copy is worse than none.
#
# Idempotent: a port already at the latest release downloads nothing.
# Pass --force to reinstall regardless, or one or more port keys to limit it.

set -u

APP_ROOT="$HOME/Applications"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"

# The port table lives beside this script in ports.conf, shared with
# launch.sh so the two cannot disagree about where a port keeps its
# data. Comments and blank lines are stripped; everything else is a row.
PORTS_CONF="$(dirname "$0")/ports.conf"
if [ ! -r "$PORTS_CONF" ]; then
  echo "ERROR: cannot read $PORTS_CONF" >&2
  exit 1
fi
PORTS=$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$PORTS_CONF")
if [ -z "$PORTS" ]; then
  echo "ERROR: $PORTS_CONF has no port rows" >&2
  exit 1
fi

FORCE=0
WANTED=""
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      echo "usage: ${0##*/} [--force] [port ...]"
      echo "ports: $(echo "$PORTS" | cut -d'|' -f1 | tr '\n' ' ')"
      exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *)  WANTED="$WANTED $arg " ;;
  esac
done

for cmd in curl unzip jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd is not installed. Run packages.sh first." >&2
    exit 1
  fi
done

# A dump is any file with one of these extensions sitting in the data
# directory. Presence only -- the hash lists live upstream and move with the
# projects, and a copy here would rot silently while looking authoritative.
DUMP_EXTS='-iname *.z64 -o -iname *.n64 -o -iname *.v64 -o -iname *.rvz -o -iname *.iso -o -iname *.gcm -o -iname *.ciso -o -iname *.gcz -o -iname *.nfs -o -iname *.wbfs -o -iname *.wia -o -iname *.tgc'

has_dump() { # has_dump <data dir>
  # shellcheck disable=SC2086  # DUMP_EXTS is a deliberate word-split arg list
  find "$1" -maxdepth 1 -type f \( $DUMP_EXTS \) 2>/dev/null | grep -q .
}

MISSING_DUMPS=""
PICKER_NOTES=""

# check_dump <name> <datastyle> <data dir> <dump> <repo>
#
# Only a shiphome port needs its dump sitting in the data directory; that is
# where libultraship looks and nowhere else. An xdg port chooses its image
# through its own file dialog and stores the absolute path, so the file stays
# in whatever library it already lives in -- copying 935 MB of GameCube disc
# into ~/.local/share to satisfy a check here would be pure waste. Those get a
# reminder on first install instead of being reported as missing.
check_dump() {
  local name="$1" datastyle="$2" dir="$3" dump="$4" repo="$5"
  if [ "$datastyle" = "shiphome" ]; then
    has_dump "$dir" || MISSING_DUMPS="$MISSING_DUMPS$name|$dir|$dump|$repo"$'\n'
  elif [ ! -e "$dir/config.json" ]; then
    PICKER_NOTES="$PICKER_NOTES$name|$dump"$'\n'
  fi
}

install_port() { # install_port <table row>
  local row="$1"
  local key name repo pattern sub datastyle assetstyle dump
  IFS='|' read -r key name repo pattern sub datastyle assetstyle dump <<<"$row"

  local app_dir="$APP_ROOT/$key"
  local data_dir="$DATA_ROOT/$sub"
  local appimage="$app_dir/$key.appimage"
  local stamp="$app_dir/.release"
  local icon="$app_dir/$key.png"

  local release tag url installed
  if ! release=$(curl -fsSL --retry 2 "https://api.github.com/repos/$repo/releases/latest"); then
    echo "$name: ERROR could not reach the GitHub API (rate limit is 60/hour per IP)" >&2
    return 1
  fi
  tag=$(jq -r '.tag_name // empty' <<<"$release")
  url=$(jq -r --arg re "$pattern" \
    '[.assets[] | select(.name | test($re; "i")) | .browser_download_url] | first // empty' \
    <<<"$release")
  if [ -z "$tag" ] || [ -z "$url" ]; then
    echo "$name: ERROR latest release has no asset matching /$pattern/" >&2
    return 1
  fi

  installed=""
  [ -r "$stamp" ] && installed=$(cat "$stamp")

  mkdir -p "$app_dir" "$data_dir"

  if [ "$FORCE" -eq 0 ] && [ "$installed" = "$tag" ] && [ -x "$appimage" ]; then
    echo "$name: $tag already installed"
    check_dump "$name" "$datastyle" "$data_dir" "$dump" "$repo"
    return 0
  fi

  local tmp
  tmp=$(mktemp -d) || return 1
  # shellcheck disable=SC2064  # $tmp must be expanded now, not at trap time
  trap "rm -rf \"${tmp:?}\"" RETURN

  if [ -z "$installed" ]; then
    echo "$name: installing $tag ..."
  elif [ "$installed" = "$tag" ]; then
    echo "$name: reinstalling $tag ..."
  else
    echo "$name: updating $installed -> $tag ..."
  fi

  local new
  if [ "$assetstyle" = "zip" ]; then
    if ! curl -fL --retry 3 -sS -o "$tmp/release.zip" "$url"; then
      echo "$name: ERROR download failed" >&2; return 1
    fi
    if ! unzip -o -q "$tmp/release.zip" -d "$tmp/unpacked"; then
      echo "$name: ERROR the release zip did not extract" >&2; return 1
    fi
    new=$(find "$tmp/unpacked" -maxdepth 2 -type f -iname '*.appimage' | head -n 1)
    # Lighthouse and SpaghettiKart ship gamecontrollerdb.txt beside the
    # appimage. It is read from the app directory, which SHIP_HOME redirects,
    # so it belongs with the data rather than with the binary. Never
    # overwritten: a controller remapped by hand outranks the shipped table.
    local db
    db=$(find "$tmp/unpacked" -maxdepth 2 -type f -name 'gamecontrollerdb.txt' | head -n 1)
    [ -n "$db" ] && [ ! -f "$data_dir/gamecontrollerdb.txt" ] &&
      cp "$db" "$data_dir/gamecontrollerdb.txt" && echo "    + gamecontrollerdb.txt"
  else
    if ! curl -fL --retry 3 -sS -o "$tmp/app.AppImage" "$url"; then
      echo "$name: ERROR download failed" >&2; return 1
    fi
    new="$tmp/app.AppImage"
  fi

  if [ -z "$new" ] || [ ! -s "$new" ]; then
    echo "$name: ERROR no appimage in the release asset" >&2; return 1
  fi
  chmod +x "$new"

  # Doubles as a smoke test: an appimage that cannot mount itself -- no FUSE,
  # a truncated download that still unzipped -- fails here, while the copy it
  # is about to replace is untouched. A pattern matching nothing still exits
  # 0, so this cannot fail merely because a release dropped its icons.
  if ! ( cd "$tmp" || exit 1; "$new" --appimage-extract 'usr/share/icons/*' ) >/dev/null 2>&1; then
    echo "$name: ERROR the new appimage will not run; keeping the installed one" >&2
    return 1
  fi
  local icon_src
  icon_src=$(find "$tmp/squashfs-root" -type f -iname '*.png' 2>/dev/null | sort | head -n 1)
  [ -n "$icon_src" ] && cp -f "$icon_src" "$icon"

  # Staged inside app_dir and renamed rather than copied over the live path:
  # /tmp is a tmpfs, so a copy from there crosses filesystems and is not
  # atomic. Interrupting this must not leave a half-written appimage.
  cp -f "$new" "$appimage.new" || return 1
  chmod +x "$appimage.new"
  mv -f "$appimage.new" "$appimage" || return 1
  printf '%s\n' "$tag" > "$stamp"

  echo "    $appimage"
  echo "    data: $data_dir${datastyle:+ ($datastyle)}"
  check_dump "$name" "$datastyle" "$data_dir" "$dump" "$repo"
}

rc=0
while IFS= read -r row; do
  [ -n "$row" ] || continue
  key="${row%%|*}"
  if [ -n "$WANTED" ]; then
    case "$WANTED" in *" $key "*) ;; *) continue ;; esac
  fi
  install_port "$row" || rc=1
done <<<"$PORTS"

if [ -n "$WANTED" ]; then
  for w in $WANTED; do
    case "$PORTS" in *"$w|"*) ;; *) echo "ERROR: no such port: $w" >&2; rc=1 ;; esac
  done
fi

if [ -n "$PICKER_NOTES" ]; then
  echo
  while IFS='|' read -r name dump; do
    [ -n "$name" ] || continue
    echo "$name picks its game image in-app on first launch."
    echo "  wants: $dump  (leave it wherever your library keeps it)"
  done <<<"$PICKER_NOTES"
fi

if [ -n "$MISSING_DUMPS" ]; then
  echo
  echo "These cannot start until you supply a game dump:"
  while IFS='|' read -r name dir dump repo; do
    [ -n "$name" ] || continue
    echo
    echo "  $name"
    echo "    wants:  $dump"
    echo "    put in: $dir"
    echo "    hashes: https://github.com/$repo"
  done <<<"$MISSING_DUMPS"
fi

exit "$rc"
