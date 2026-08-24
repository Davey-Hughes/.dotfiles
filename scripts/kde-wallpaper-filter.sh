#!/usr/bin/env bash
# git clean/smudge filter for the Plasma appletsrc: keep local wallpaper paths
# out of the repo without losing them from the working copy.
#
#     kde-wallpaper-filter.sh clean   < worktree-file > blob-content
#     kde-wallpaper-filter.sh smudge  < blob-content  > worktree-file
#
# Wired up per clone by install.sh (git_settings); .gitattributes names the
# driver. Both halves are configured together -- see the warning below.
#
# WHY BOTH HALVES. The first version of this was `clean = sed -d` with `smudge =
# cat`: it deleted Image=/SlidePaths= on the way in and restored nothing on the
# way out. That is not merely lopsided, it is destructive, because git compares
# the CLEANED worktree against the stored blob. With the lines stripped before
# the comparison, a file that still carried them read as *unchanged* -- so
# `git status` stayed quiet and git felt free to re-materialise the file. Every
# checkout, rebase or branch switch then wiped the live wallpaper with no
# warning and no conflict. It did exactly that on 2026-08-22, mid-rebase, and
# Plasma came back on defaults.
#
# So the pair has to round-trip. clean still strips (the repo is public and the
# slideshow rewrites the path every 30 minutes, which would otherwise leave the
# file permanently dirty), but it also records what it stripped in an untracked
# per-machine sidecar, and smudge puts those lines back under the sections they
# came from. The blob stays clean; the desktop keeps its wallpaper.
#
# The sidecar lives outside the repo on purpose: it is machine state, not
# content, and inside the tree a `git clean -fdx` would take it out.
set -euo pipefail

SIDECAR=${KDE_WALLPAPER_SIDECAR:-${XDG_STATE_HOME:-$HOME/.local/state}/dotfiles/kde-wallpaper.conf}

# The keys carrying a local path. Image= is the slideshow's current picture,
# SlidePaths= the folders it rotates through.
KEYS='^(Image|SlidePaths)='

# Strip the wallpaper keys from stdin, and remember them (with the KConfig
# section each sat under) for smudge.
#
# The sidecar is only replaced when this input actually had something to record.
# A file that legitimately has no wallpaper lines -- which is exactly what the
# worktree looks like in the instant after a checkout -- must not be allowed to
# erase the memory, or the loss just moves one file over.
cmd_clean() {
  local tmp
  tmp=$(mktemp) || exit 1
  awk -v out="$tmp" -v keys="$KEYS" '
    /^\[.*\]$/ { sec = $0; print; next }
    $0 ~ keys && sec != "" {
      if (!(sec in seen)) { seen[sec] = 1; order[++n] = sec }
      saved[sec] = saved[sec] $0 "\n"
      next
    }
    { print }
    END {
      for (i = 1; i <= n; i++) printf("%s\n%s", order[i], saved[order[i]]) > out
      if (n > 0) close(out)
    }
  '
  if [ -s "$tmp" ]; then
    mkdir -p "$(dirname "$SIDECAR")"
    mv -f "$tmp" "$SIDECAR"
  else
    rm -f "$tmp"
  fi
}

# Put the remembered lines back, each under its own section header.
#
# Where the sidecar has an entry for a section it wins outright: any Image=/
# SlidePaths= arriving from the blob in that section is dropped rather than
# duplicated. That keeps the output identical whether or not some clone
# committed the lines without the filter configured.
cmd_smudge() {
  # No sidecar (a fresh machine) is not an error, just nothing to restore. The
  # -s test also guards awk's FNR==NR pass split, which mistakes stdin for the
  # first file when that file is empty.
  [ -s "$SIDECAR" ] || exec cat

  awk -v keys="$KEYS" '
    FNR == NR {
      if ($0 ~ /^\[.*\]$/) {
        sec = $0
        if (!(sec in seen)) { seen[sec] = 1; order[++n] = sec }
      } else if (sec != "" && $0 ~ keys) {
        saved[sec] = saved[sec] $0 "\n"
      }
      next
    }
    /^\[.*\]$/ {
      cur = $0
      print
      if (cur in saved) { printf("%s", saved[cur]); done[cur] = 1 }
      next
    }
    (cur in saved) && $0 ~ keys { next }
    { print }
    END {
      # A section the blob no longer has at all -- KConfig drops a group once it
      # is empty. Re-append it rather than lose the wallpaper with it.
      for (i = 1; i <= n; i++) {
        s = order[i]
        if (!(s in done) && (s in saved)) { print ""; print s; printf("%s", saved[s]) }
      }
    }
  ' "$SIDECAR" -
}

case "${1:-}" in
  clean)  cmd_clean ;;
  smudge) cmd_smudge ;;
  *) printf 'usage: %s clean|smudge\n' "${0##*/}" >&2; exit 2 ;;
esac
