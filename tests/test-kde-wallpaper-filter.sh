#!/usr/bin/env bash
# The kde-wallpaper filter round-trips: it keeps local wallpaper paths OUT of
# git and IN the working copy.
#
#     ./tests/test-kde-wallpaper-filter.sh
#
# Why this suite exists. The filter started life as `clean = sed -d`, `smudge =
# cat` -- lossy in one direction and a no-op in the other. That combination is
# worse than it looks, because git compares the CLEANED worktree against the
# blob: with the wallpaper lines stripped on the way in, a file carrying them
# reads as *unchanged*. So git felt free to re-materialise it, and every
# checkout, rebase or branch switch silently deleted the live wallpaper -- no
# warning, no conflict, nothing in `git status`. It cost a real desktop its
# wallpapers on 2026-08-22 during a rebase.
#
# The fix is a smudge that re-injects the lines from an untracked per-machine
# sidecar, which the clean side keeps up to date. That makes the pair lossless,
# so what is asserted below is BOTH halves: nothing leaks into the blob, and
# nothing is lost from the worktree.
#
# Everything runs in a throwaway repo under $TMPDIR with the sidecar redirected
# there too, so a run never reads or writes the real ~/.local/state copy.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

FILTER="$PWD/scripts/kde-wallpaper-filter.sh"
APPLETSRC='os/endeavour/config/plasma-org.kde.plasma.desktop-appletsrc'

if [ ! -x "$FILTER" ]; then
  fail "scripts/kde-wallpaper-filter.sh is missing or not executable"
  summary "kde-wallpaper-filter"
  exit $?
fi

TMP=$(mktemp -d)
trap 'rm -rf "${TMP:?}"' EXIT
SIDECAR="$TMP/sidecar.conf"
export KDE_WALLPAPER_SIDECAR="$SIDECAR"

# The two paths a real desktop had: a space, an apostrophe and a comma-separated
# list, because those are exactly what a naive re-injection mangles.
IMG2="file:///home/d/Wallpapers/High Res/Anime/Frieren/1344010.jpeg"
IMG55="file:///home/d/Wallpapers/High Res/Anime/Miss Kobayashi's Dragon Maid/02b.jpg"
SLIDES="/home/d/Wallpapers/High Res/Anime,/home/d/Wallpapers/High Res/Video Games"

# A miniature appletsrc: two desktop containments with wallpaper, a panel
# containment without, and a trailing section that must not absorb an injection.
write_live() { # write_live <path>
  tee "$1" >/dev/null <<EOF
[Containments][2]
wallpaperplugin=org.kde.slideshow

[Containments][2][Wallpaper][org.kde.slideshow][General]
Image=$IMG2
SlideInterval=1800
SlidePaths=$SLIDES

[Containments][29]
plugin=org.kde.panel

[Containments][55][Wallpaper][org.kde.slideshow][General]
Image=$IMG55
SlideInterval=1800

[ScreenMapping]
screenMapping=
EOF
}

new_repo() { # new_repo -- echoes the path to a fresh repo wired like the real one
  local r="$TMP/repo"
  rm -rf "${r:?}"
  mkdir -p "$r/$(dirname "$APPLETSRC")"
  git -C "$r" init -q
  git -C "$r" config user.email t@t; git -C "$r" config user.name t
  git -C "$r" config filter.kde-wallpaper.clean  "'$FILTER' clean"
  git -C "$r" config filter.kde-wallpaper.smudge "'$FILTER' smudge"
  printf '%s filter=kde-wallpaper\n' "$APPLETSRC" | tee "$r/.gitattributes" >/dev/null
  echo "$r"
}

# --- 1. the privacy half: nothing local reaches the blob -----------------------
section "clean: no wallpaper paths in the committed blob"
R=$(new_repo)
write_live "$R/$APPLETSRC"
git -C "$R" add -A && git -C "$R" commit -qm init
leaked=$(git -C "$R" cat-file blob "HEAD:$APPLETSRC" | grep -cE '^(Image|SlidePaths)=')
if [ "${leaked:-0}" -eq 0 ]; then
  ok "committed blob carries no Image=/SlidePaths= lines"
else
  fail "$leaked wallpaper line(s) leaked into the blob"
fi

# --- 2. the half that was broken: a checkout must not eat the worktree ---------
# This is the regression. `git checkout` re-materialises the file from the blob;
# with a no-op smudge that silently deleted the live wallpaper.
section "smudge: a checkout preserves the live wallpaper"
git -C "$R" checkout -qb other
printf '[Containments][2]\nwallpaperplugin=org.kde.image\n' | tee "$R/$APPLETSRC" >/dev/null
git -C "$R" commit -qam other
git -C "$R" checkout -q -            # back to main: the file is rewritten from the blob

before=$FAILURES
grep -qF "Image=$IMG2"   "$R/$APPLETSRC" || fail "containment 2's Image= did not survive the checkout"
grep -qF "Image=$IMG55"  "$R/$APPLETSRC" || fail "containment 55's Image= did not survive the checkout"
grep -qF "SlidePaths=$SLIDES" "$R/$APPLETSRC" || fail "SlidePaths= did not survive the checkout"
[ "$FAILURES" -eq "$before" ] && ok "Image= and SlidePaths= are back in the working copy"

# --- 3. each line goes back under its OWN section ------------------------------
# Appending blindly would put containment 55's wallpaper under containment 2,
# or drop both into [ScreenMapping] at the end of the file.
section "smudge: each line lands under its own section"
sec_of() { # sec_of <file> <needle> -- the [Section] the matching line sits under
  awk -v needle="$2" '/^\[.*\]$/ { s = $0; next } index($0, needle) { print s; exit }' "$1"
}
before=$FAILURES
[ "$(sec_of "$R/$APPLETSRC" "Image=$IMG2")" = '[Containments][2][Wallpaper][org.kde.slideshow][General]' ] ||
  fail "containment 2's Image= landed under $(sec_of "$R/$APPLETSRC" "Image=$IMG2")"
[ "$(sec_of "$R/$APPLETSRC" "Image=$IMG55")" = '[Containments][55][Wallpaper][org.kde.slideshow][General]' ] ||
  fail "containment 55's Image= landed under $(sec_of "$R/$APPLETSRC" "Image=$IMG55")"
[ "$(grep -c "^Image=" "$R/$APPLETSRC")" -eq 2 ] ||
  fail "expected exactly 2 Image= lines, found $(grep -c '^Image=' "$R/$APPLETSRC")"
[ "$FAILURES" -eq $before ] && ok "both wallpapers re-injected under the right containment, no duplicates"

# --- 4. a fresh machine has no sidecar, and that is not an error ---------------
section "smudge: no sidecar yet is a clean no-op"
rm -f "$SIDECAR"
R2=$(new_repo)
write_live "$R2/$APPLETSRC"
git -C "$R2" add -A && git -C "$R2" commit -qm init
rm -f "$SIDECAR"                     # forget what clean just learned
rm -f "$R2/$APPLETSRC"
if git -C "$R2" checkout -q -- "$APPLETSRC" 2>"$TMP/err"; then
  if [ "$(grep -cE '^(Image|SlidePaths)=' "$R2/$APPLETSRC")" -eq 0 ]; then
    ok "checkout succeeds and injects nothing"
  else
    fail "wallpaper lines appeared with no sidecar present"
  fi
else
  fail "checkout failed with no sidecar: $(cat "$TMP/err")"
fi

# --- 5. clean must not forget what it already knows ----------------------------
# The moment after a bad checkout, the worktree file has NO wallpaper lines. If
# clean overwrote the sidecar from that, the memory would be gone for good --
# the same one-way loss, just moved one file over.
section "clean: a wallpaper-less file does not erase the sidecar"
R3=$(new_repo)
write_live "$R3/$APPLETSRC"
git -C "$R3" add -A && git -C "$R3" commit -qm init
[ -s "$SIDECAR" ] || fail "clean did not record the wallpaper lines at all"
printf '[Containments][2]\nwallpaperplugin=org.kde.slideshow\n' | tee "$R3/$APPLETSRC" >/dev/null
git -C "$R3" add -A >/dev/null 2>&1
if grep -qF "Image=$IMG2" "$SIDECAR"; then
  ok "sidecar still remembers the wallpaper"
else
  fail "clean erased the sidecar when handed a file with no wallpaper lines"
fi

summary "kde-wallpaper-filter"
