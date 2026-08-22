#!/usr/bin/env bash
# save-bridge.sh, exercised against a synthetic sync tree.
#
# The bridge writes save files, and every interesting decision it makes is
# about which of several copies is authoritative. That is not something to
# find out on real saves, so this builds a throwaway tree in $TMPDIR, drives
# the script through every branch of that decision, and asserts on the files
# left behind.
#
# Every root the script reads is overridable by environment variable, which is
# what makes this possible without touching /mnt/user at all.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

BRIDGE="$PWD/os/unraid/save-bridge.sh"
[ -x "$BRIDGE" ] || { fail "not executable: $BRIDGE"; summary "save-bridge"; exit 1; }

TREE=""
TREES=()
# Every tree, not just the current one: new_tree reassigns TREE, so cleaning
# up only "$TREE" would leak one directory per test into $TMPDIR.
cleanup() { local t; for t in ${TREES+"${TREES[@]}"}; do rm -rf "${t:?}"; done; }
trap cleanup EXIT

# A fresh tree per test. Nothing carries over between them -- the whole point
# of most of these cases is what the state file did or did not record.
new_tree() {
  TREE=$(mktemp -d)
  TREES+=("$TREE")
  mkdir -p "$TREE/ManicEMU/gba" \
           "$TREE/Emulator Saves/retroarch/saves/mGBA" \
           "$TREE/Emulator Saves/retroarch/saves/Snes9x" \
           "$TREE/MiSTer/saves/SNES"
}

mkfile() { mkdir -p "$(dirname "$1")"; printf '%s' "$2" > "$1"; }

# QUIET_SECONDS=0 defeats the "file may still be being written" guard, which
# would otherwise skip every file this test just created.
run_bridge() {
  SYNC_ROOT="$TREE" \
  MANIC_ROOT="$TREE/ManicEMU" \
  RETRO_ROOT="$TREE/Emulator Saves/retroarch" \
  MISTER_ROOT="$TREE/MiSTer/saves" \
  WORK_DIR="$TREE/.save-bridge" \
  QUIET_SECONDS=0 \
  LOCK_FILE="$TREE/lock" \
  bash "$BRIDGE" "$@" 2>&1
}

# assert_file <path> <expected content> <label>
assert_file() {
  if [ ! -e "$1" ]; then fail "$3 -- missing: ${1#"$TREE"/}"; return; fi
  local got; got=$(cat "$1")
  if [ "$got" = "$2" ]; then ok "$3"; else
    fail "$3"; note "expected '$2', got '$got'"
  fi
}

assert_absent() {
  if [ -e "$1" ]; then fail "$2 -- should not exist: ${1#"$TREE"/}"; else ok "$2"; fi
}

M="ManicEMU/gba"
R="Emulator Saves/retroarch/saves/mGBA"

section "dry run writes nothing"
new_tree
mkfile "$TREE/$M/Game.sav" "manic-v1"
run_bridge >/dev/null
assert_absent "$TREE/$R/Game.srm" "no copy without --apply"
assert_absent "$TREE/.save-bridge/state.tsv" "no state without --apply"

section "a save present on one side only is copied across"
new_tree
mkfile "$TREE/$M/Game.sav" "manic-v1"
run_bridge --apply >/dev/null
assert_file "$TREE/$R/Game.srm" "manic-v1" "manic -> retroarch (new)"

section "the reverse direction works too"
new_tree
mkfile "$TREE/$R/Game.srm" "retro-v1"
run_bridge --apply >/dev/null
assert_file "$TREE/$M/Game.sav" "retro-v1" "retroarch -> manic (new)"

section "a second run with nothing changed copies nothing"
new_tree
mkfile "$TREE/$M/Game.sav" "manic-v1"
run_bridge --apply >/dev/null
out=$(run_bridge --apply)
if printf '%s' "$out" | grep -q '^  COPIED'; then
  fail "idempotent second run"; note "$(printf '%s' "$out" | grep '^  COPIED')"
else
  ok "idempotent second run"
fi

section "one side changed since the record: it wins"
new_tree
mkfile "$TREE/$M/Game.sav" "v1"
run_bridge --apply >/dev/null          # records v1 for both
mkfile "$TREE/$M/Game.sav" "manic-v2"  # only manic moves
run_bridge --apply >/dev/null
assert_file "$TREE/$R/Game.srm" "manic-v2" "changed side propagates"

section "both sides changed: copy neither, stash both, exit 2"
new_tree
mkfile "$TREE/$M/Game.sav" "v1"
run_bridge --apply >/dev/null
mkfile "$TREE/$M/Game.sav" "manic-v2"
mkfile "$TREE/$R/Game.srm" "retro-v2"
run_bridge --apply >/dev/null; rc=$?
[ "$rc" -eq 2 ] && ok "exit status 2" || fail "exit status 2 -- got $rc"
assert_file "$TREE/$M/Game.sav" "manic-v2" "manic left untouched"
assert_file "$TREE/$R/Game.srm" "retro-v2" "retroarch left untouched"
if find "$TREE/.save-bridge/conflicts" -name 'manic.sav' 2>/dev/null | grep -q .; then
  ok "both copies stashed"
else
  fail "both copies stashed"
fi

section "a first-run tie needs --seed and honours it"
new_tree
mkfile "$TREE/$M/Game.sav" "manic-v1"
mkfile "$TREE/$R/Game.srm" "retro-v1"
run_bridge --apply >/dev/null; rc=$?
[ "$rc" -eq 2 ] && ok "unseeded tie is a conflict" || fail "unseeded tie is a conflict -- got $rc"

new_tree
mkfile "$TREE/$M/Game.sav" "manic-v1"
mkfile "$TREE/$R/Game.srm" "retro-v1"
run_bridge --apply --seed=manic >/dev/null
assert_file "$TREE/$R/Game.srm" "manic-v1" "--seed=manic breaks the tie"

summary "save-bridge"
