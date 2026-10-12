#!/usr/bin/env bash
# os/steamdeck/fstab.sh, exercised against scratch copies of an fstab.
#
# The script rewrites /etc/fstab as root, and the Deck needs it again after
# every SteamOS update -- so the property that matters is what a SECOND run
# does, to a file that a first run (or the append-only script it replaced) has
# already been at. None of that is something to find out on a real /etc/fstab.
#
# Everything here goes through `--print <dest> <src>`, which reads two files and
# writes neither. The apply path is not exercised: past the rewrite it is four
# sudo lines, and there is no safe machine to run them on.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

FSTAB_SH="$PWD/os/steamdeck/fstab.sh"
SRC="$PWD/os/steamdeck/fstab"
[ -x "$FSTAB_SH" ] || { fail "not executable: $FSTAB_SH"; summary "steamdeck-fstab"; exit 1; }

T=$(mktemp -d)
trap 'rm -rf "${T:?}"' EXIT

# ALWAYS --print, and always both paths. Run bare, the script under test
# rewrites this machine's /etc/fstab through sudo.
render() { "$FSTAB_SH" --print "$1" "$2"; }

entries()      { grep -vE '^[[:space:]]*(#|$)' "$1"; }
mount_points() { entries "$1" | awk '{ print $2 }'; }

# How many entries of <file> claim <mount point>.
#
# The mount point reaches awk through the environment and not `-v`: -v expands
# backslash escapes in its value, and fstab spells a space as \040 -- so `-v`
# would go looking for "media 0" in a file that says "media\0400" and count 0.
count_mounts() { # count_mounts <file> <mount point>
  M="$2" awk '$0 !~ /^[[:space:]]*#/ && $2 == ENVIRON["M"]' "$1" | wc -l
}

# Every mount point <src> lists is claimed by exactly one entry of <file>.
assert_each_once() { # assert_each_once <file> <label> [src]
  local file="$1" label="$2" src="${3:-$SRC}" m n before=$FAILURES
  while IFS= read -r m; do
    n=$(count_mounts "$file" "$m")
    [ "$n" -eq 1 ] || fail "$label -- $m has $n entries"
  done < <(mount_points "$src")
  [ "$FAILURES" -eq "$before" ] && ok "$label"
}

assert_same() { # assert_same <file> <file> <label>
  if cmp -s "$1" "$2"; then
    ok "$3"
  else
    fail "$3"
    note "$(diff "$1" "$2" | head -5)"
  fi
}

# The three entries are what SteamOS 3.8 ships; the comments stand in for its
# header, which is only here to be something that must come through untouched.
cat > "$T/stock" <<'EOF'
# Static information about the filesystems.
# See fstab(5) for details.

# <file system> <dir> <type> <options> <dump> <pass>
/dev/disk/by-partsets/self/efi    /efi    vfat    defaults,nofail,umask=0077,x-systemd.automount,x-systemd.idle-timeout=1min 0       2
/dev/disk/by-partsets/shared/esp  /esp    vfat    defaults,nofail,umask=0077,x-systemd.automount,x-systemd.idle-timeout=1min 0       2
/dev/disk/by-partsets/shared/home /home   ext4    defaults,nofail,x-systemd.growfs 0       2
EOF

# Without this every assert_each_once below loops over nothing and passes.
if [ "$(mount_points "$SRC" | wc -l)" -eq 0 ]; then
  fail "os/steamdeck/fstab lists no mounts, so nothing below would be tested"
  summary "steamdeck-fstab"
  exit 1
fi

# --- the real source, into a stock file ---------------------------------------
section "first run, on a stock fstab"
render "$T/stock" "$SRC" > "$T/once"; rc=$?
[ "$rc" -eq 0 ] && ok "--print exits 0" || fail "--print exits 0 -- got $rc"

head -n "$(wc -l < "$T/stock")" "$T/once" > "$T/once.head"
assert_same "$T/stock" "$T/once.head" "every stock line is kept, in place"
assert_each_once "$T/once" "each share is added exactly once"

# Byte for byte, so this also pins that the \040 in "media\0400" survives awk.
sed -n '/^# BEGIN dotfiles /,/^# END dotfiles /p' "$T/once" | grep -v '^#' > "$T/once.block"
entries "$SRC" > "$T/src.entries"
assert_same "$T/src.entries" "$T/once.block" "the block holds the source's entries verbatim"

section "second run"
render "$T/once" "$SRC" > "$T/twice"
assert_same "$T/once" "$T/twice" "running it again changes nothing"

# --- a file the old script has already been at ---------------------------------
# It was `sudo tee -a /etc/fstab < ./fstab`: the whole source, comments included,
# on the end, once per run.
section "after the old blind append"
cat "$T/stock" "$SRC" > "$T/legacy1"
render "$T/legacy1" "$SRC" > "$T/legacy1.out"
assert_each_once "$T/legacy1.out" "one old append: each share ends up listed once"

cat "$T/stock" "$SRC" "$SRC" > "$T/legacy2"
render "$T/legacy2" "$SRC" > "$T/legacy2.out"
assert_each_once "$T/legacy2.out" "two old appends: each share ends up listed once"
assert_each_once "$T/legacy2.out" "the stock mounts are still there" "$T/stock"

render "$T/legacy2.out" "$SRC" > "$T/legacy2.again"
assert_same "$T/legacy2.out" "$T/legacy2.again" "and a further run changes nothing"

# --- the source changing under an existing block --------------------------------
section "editing the source"
printf '%s\n' '# two shares' 'nas:/a /mnt/a nfs ro 0 0' 'nas:/b /mnt/b nfs ro 0 0' > "$T/src.v1"
printf '%s\n' '# a is now rw' 'nas:/a /mnt/a nfs rw 0 0' 'nas:/b /mnt/b nfs ro 0 0' > "$T/src.v2"
printf '%s\n' '# b is gone'   'nas:/a /mnt/a nfs rw 0 0'                            > "$T/src.v3"
printf '%s\n' '# nothing left' ''                                                   > "$T/src.none"

render "$T/stock" "$T/src.v1" > "$T/v1"
render "$T/v1" "$T/src.v2" > "$T/v2"
assert_each_once "$T/v2" "an edited entry replaces the old one" "$T/src.v2"
grep -q '^nas:/a /mnt/a nfs rw 0 0$' "$T/v2" \
  && ok "the surviving entry is the edited one" \
  || fail "the surviving entry is the edited one"

render "$T/v2" "$T/src.v3" > "$T/v3"
[ "$(count_mounts "$T/v3" /mnt/b)" -eq 0 ] \
  && ok "an entry dropped from the source leaves the file" \
  || fail "an entry dropped from the source leaves the file"

render "$T/v3" "$T/src.none" > "$T/none"
assert_same "$T/stock" "$T/none" "a source with no entries takes the block out, leaving stock"

# --- lines that are not ours -----------------------------------------------------
section "entries that are not ours"
{ cat "$T/v1"; echo '/dev/sdb1 /mnt/usb ext4 noauto 0 0'; } > "$T/foreign"
render "$T/foreign" "$T/src.v1" > "$T/foreign.out"
[ "$(count_mounts "$T/foreign.out" /mnt/usb)" -eq 1 ] \
  && ok "an entry added by hand below the block is kept" \
  || fail "an entry added by hand below the block is kept"
render "$T/foreign.out" "$T/src.v1" > "$T/foreign.again"
assert_same "$T/foreign.out" "$T/foreign.again" "and a further run changes nothing"

# --- inputs that must not be trusted ---------------------------------------------
section "degenerate inputs"

# The `NR == FNR` trap: with a zero-byte first file, awk's usual two-file idiom
# takes the SECOND file for the first. Every stock line would be adopted as
# "ours" and moved into the block, then deleted with it by the next run.
: > "$T/src.zero"
render "$T/stock" "$T/src.zero" > "$T/zero"
assert_same "$T/stock" "$T/zero" "a zero-byte source leaves the file exactly as it was"

# A BEGIN line with no END after it. Taking "the block" to run to end-of-file
# would delete whatever a hand edit put below it.
{ cat "$T/stock"; grep -m1 '^# BEGIN dotfiles ' "$T/once"; echo '/dev/sdb1 /mnt/usb ext4 noauto 0 0'; } > "$T/open"
out=$(render "$T/open" "$SRC" 2>/dev/null); rc=$?
[ "$rc" -ne 0 ] && ok "an unterminated block is refused" \
                || fail "an unterminated block is refused -- exited 0"
[ -z "$out" ] && ok "and nothing is printed that could be installed" \
              || { fail "and nothing is printed that could be installed"; note "${out%%$'\n'*}"; }

out=$(render "$T/absent" "$SRC" 2>/dev/null); rc=$?
[ "$rc" -ne 0 ] && [ -z "$out" ] \
  && ok "a missing destination is an error, not an empty fstab" \
  || fail "a missing destination is an error, not an empty fstab -- rc=$rc"

out=$(render "$T/stock" "$T/absent" 2>/dev/null); rc=$?
[ "$rc" -ne 0 ] && [ -z "$out" ] \
  && ok "a missing source is an error, not a file with the block removed" \
  || fail "a missing source is an error, not a file with the block removed -- rc=$rc"

summary "steamdeck-fstab"
