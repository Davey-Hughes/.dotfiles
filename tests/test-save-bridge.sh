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
  BRIDGE_BIN=""
  mkdir -p "$TREE/ManicEMU/gba" \
           "$TREE/Emulator Saves/retroarch/saves/mGBA" \
           "$TREE/Emulator Saves/retroarch/saves/Snes9x" \
           "$TREE/MiSTer/saves/SNES"
}

mkfile() { mkdir -p "$(dirname "$1")"; printf '%s' "$2" > "$1"; }

# The hash the script would record for a file holding exactly this string.
sha_of() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

# Some cases are about the MAPPINGS table rather than the tree, and need a table
# the real one deliberately does not have: a three-participant row, a row naming
# an undeclared participant, a table mentioning a participant nowhere. Those run
# against a throwaway COPY of the script with the table rewritten. Editing the
# repo's MAPPINGS to test it would be editing the thing under test, and the
# MiSTer's GBA saves have never been format-verified anyway.
#
# The cmp guard earns its keep: rename a MAPPINGS row and these seds stop
# matching, and without it the affected tests would go on passing while quietly
# exercising the unmodified script.
variant_bridge() { # variant_bridge <label> <sed script>
  BRIDGE_BIN="$TREE/save-bridge-variant.sh"
  sed "$2" "$BRIDGE" > "$BRIDGE_BIN"
  chmod +x "$BRIDGE_BIN"
  cmp -s "$BRIDGE" "$BRIDGE_BIN" && \
    fail "$1 -- the MAPPINGS rewrite matched nothing, so this ran the wrong script"
  return 0
}

# The gba row with the MiSTer bolted onto it: manic + retroarch + mister.
gba_3p() { # gba_3p <label>
  variant_bridge "$1" 's#retroarch:saves/mGBA:srm"$#retroarch:saves/mGBA:srm|mister:GBA:sav"#'
}

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
  bash "${BRIDGE_BIN:-$BRIDGE}" "$@" 2>&1
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
MG="MiSTer/saves/GBA"
STATE=".save-bridge/state.tsv"

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
run_bridge --apply --seed=manic >/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "an honoured --seed exits 0" \
                || fail "an honoured --seed exits 0 -- got $rc"
assert_file "$TREE/$R/Game.srm" "manic-v1" "--seed=manic breaks the tie"

# --- regressions -------------------------------------------------------------
#
# Everything above pins two-participant behaviour. Everything below is about a
# THIRD participant arriving, which is where the bridge was silently destroying
# saves: a newcomer with an ancient copy and no record of its own was read as
# "the one that changed" and won.

section "a v1 state row must not answer for a third participant"
# The Critical case, end to end. state.tsv v1 (system/game/hash, no participant
# column) was written by the two-party bridge and records only that manic and
# retroarch both held that hash. It says nothing about the MiSTer. Reading it as
# if it did hands mister a hash it never wrote, so mister's ancient copy
# mismatches, mister is judged the one that moved, and it overwrites the two
# participants whose saves are actually current -- exit 0, no warning.
new_tree; gba_3p "v1 row vs a third participant"
mkfile "$TREE/$M/Game.sav"  "shared-v1"
mkfile "$TREE/$R/Game.srm"  "shared-v1"
mkfile "$TREE/$MG/Game.sav" "mister-ancient"
mkdir -p "$TREE/.save-bridge"
printf 'gba\tGame\t%s\n' "$(sha_of "shared-v1")" > "$TREE/$STATE"
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 2 ] && ok "v1 row + third participant is a conflict" \
                || fail "v1 row + third participant is a conflict -- got $rc"
assert_file "$TREE/$M/Game.sav"  "shared-v1"      "manic not overwritten by the newcomer"
assert_file "$TREE/$R/Game.srm"  "shared-v1"      "retroarch not overwritten by the newcomer"
assert_file "$TREE/$MG/Game.sav" "mister-ancient" "the stale copy stayed where it was"
if printf '%s' "$out" | grep -q '^  COPIED'; then
  fail "nothing copied at all"; note "$(printf '%s' "$out" | grep '^  COPIED')"
else
  ok "nothing copied at all"
fi

section "a participant with no record never counts as the one that moved"
# Cause B on its own, with no v1 row involved: proper per-participant records
# exist for manic and retroarch, both agree with them, and only the newcomer
# differs. "No record" is not evidence of progress, so this is a conflict rather
# than a win for the newcomer.
new_tree; gba_3p "newcomer with no record"
mkfile "$TREE/$M/Game.sav" "v1"
mkfile "$TREE/$R/Game.srm" "v1"
mkfile "$TREE/$MG/Game.sav" "v1"
run_bridge --apply >/dev/null                       # records v1 for all three
grep -v $'\tmister\t' "$TREE/$STATE" > "$TREE/state.keep" && mv "$TREE/state.keep" "$TREE/$STATE"
mkfile "$TREE/$MG/Game.sav" "mister-ancient"        # newcomer, no record, differs
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 2 ] && ok "unrecorded participant does not win" \
                || fail "unrecorded participant does not win -- got $rc"
assert_file "$TREE/$M/Game.sav" "v1" "manic survives an unrecorded newcomer"
assert_file "$TREE/$R/Game.srm" "v1" "retroarch survives an unrecorded newcomer"
printf '%s' "$out" | grep -q 'no record exists for mister' \
  && ok "the message names the participant with no record" \
  || { fail "the message names the participant with no record"; note "$out"; }

section "mixed evidence beats --seed"
# One participant demonstrably moved AND another has no record. --seed exists to
# break a first-run tie, and this is not one: honouring it here would let a
# stale newcomer beat progress the record can prove. It must still conflict.
new_tree; gba_3p "mixed evidence"
mkfile "$TREE/$M/Game.sav" "v1"
mkfile "$TREE/$R/Game.srm" "v1"
mkfile "$TREE/$MG/Game.sav" "v1"
run_bridge --apply >/dev/null
grep -v $'\tmister\t' "$TREE/$STATE" > "$TREE/state.keep" && mv "$TREE/state.keep" "$TREE/$STATE"
mkfile "$TREE/$M/Game.sav"  "manic-v2"        # moved, and the record proves it
mkfile "$TREE/$MG/Game.sav" "mister-ancient"  # no record at all
out=$(run_bridge --apply --seed=mister); rc=$?
[ "$rc" -eq 2 ] && ok "--seed does not resolve mixed evidence" \
                || fail "--seed does not resolve mixed evidence -- got $rc"
assert_file "$TREE/$M/Game.sav" "manic-v2" "the participant that moved is untouched"
assert_file "$TREE/$R/Game.srm" "v1"        "the seeded newcomer copied nowhere"

section "--seed is refused while any participant still has a record"
# The other half of the rule above, and the half --seed itself reopened. Here
# NOTHING has moved: manic and retroarch both agree with their own records,
# which is positive proof neither of them moved, and only the newcomer is
# unknown. The seed branch used to fire on "nothing changed and someone is
# unknown", so --seed=mister handed the run to the newcomer and fanned its
# ancient copy over two saves the state file could prove were current --
# silently, on exit 0, through the very flag the header documents for onboarding
# a new participant.
#
# A seed settles a FIRST RUN, where no participant has a record to override. A
# record saying "this one did not move" outranks being told who wins.
new_tree; gba_3p "seed vs participants that have records"
mkfile "$TREE/$M/Game.sav"  "v1"
mkfile "$TREE/$R/Game.srm"  "v1"
mkfile "$TREE/$MG/Game.sav" "v1"
run_bridge --apply >/dev/null                       # records v1 for all three
grep -v $'\tmister\t' "$TREE/$STATE" > "$TREE/state.keep" && mv "$TREE/state.keep" "$TREE/$STATE"
mkfile "$TREE/$MG/Game.sav" "mister-ancient"        # unknown to the state, and differs
out=$(run_bridge --apply --seed=mister); rc=$?
[ "$rc" -eq 2 ] && ok "--seed does not beat a participant with a record" \
                || fail "--seed does not beat a participant with a record -- got $rc"
assert_file "$TREE/$M/Game.sav"  "v1"             "manic keeps what its record proves"
assert_file "$TREE/$R/Game.srm"  "v1"             "retroarch keeps what its record proves"
assert_file "$TREE/$MG/Game.sav" "mister-ancient" "the seeded newcomer stayed put"
if printf '%s' "$out" | grep -q '^  COPIED'; then
  fail "a refused seed copies nothing"; note "$(printf '%s' "$out" | grep '^  COPIED')"
else
  ok "a refused seed copies nothing"
fi
# The reader has to be told the seed was seen and deliberately not used --
# otherwise the conflict looks like the seed was simply forgotten.
printf '%s' "$out" | grep -q 'seed=mister was NOT honoured' \
  && ok "the log says the seed was refused, and why" \
  || { fail "the log says the seed was refused, and why"; note "$out"; }

section "a missing participant folder skips that row, not the run"
# The MiSTer's saves folder did not exist for days after its mapping row was
# written. Aborting the whole run there stops bridging the healthy gba row too,
# so a working sync dies quietly while the ten-minute cron mails a failure.
new_tree
rm -rf "${TREE:?}/MiSTer"
mkfile "$TREE/$M/Game.sav" "manic-v1"
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 0 ] && ok "a missing folder is not a failed run" \
                || fail "a missing folder is not a failed run -- got $rc"
assert_file "$TREE/$R/Game.srm" "manic-v1" "the healthy row still bridges"
printf '%s' "$out" | grep -q "skipping mapping row 'snes'" \
  && ok "the skipped row is named" \
  || { fail "the skipped row is named"; note "$out"; }
printf '%s' "$out" | grep -q "$TREE/MiSTer/saves" \
  && ok "the missing path is named" \
  || { fail "the missing path is named"; note "$out"; }

section "a mapping row naming an undeclared participant is still fatal"
new_tree
variant_bridge "undeclared participant" \
  's#retroarch:saves/mGBA:srm"$#retroarch:saves/mGBA:srm|ghost:GBA:sav"#'
mkfile "$TREE/$M/Game.sav" "manic-v1"
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 1 ] && ok "undeclared participant exits 1" \
                || fail "undeclared participant exits 1 -- got $rc"
printf '%s' "$out" | grep -q 'undeclared participant: ghost' \
  && ok "undeclared participant is named" \
  || { fail "undeclared participant is named"; note "$out"; }

section "a failed copy is not recorded as a success"
# The winner is right, but only some of the fan-out lands. Recording the
# winner's hash for the participant that never received it makes that
# participant the sole mismatch next run, so it wins and its stale bytes are
# propagated back over the progress that just won. The state must not move.
#
# The failure is injected by putting a regular FILE where mister's per-system
# directory belongs: transfer's mkdir -p and cp then both fail, for root as
# well as for anyone else.
new_tree; gba_3p "failed copy"
mkfile "$TREE/$M/Game.sav"  "v1"
mkfile "$TREE/$R/Game.srm"  "v1"
mkfile "$TREE/$MG/Game.sav" "v1"
run_bridge --apply >/dev/null                  # records v1 for all three
rm -rf "${TREE:?}/$MG"; : > "$TREE/$MG"        # mister's directory is now a file
mkfile "$TREE/$M/Game.sav" "manic-v2"          # manic legitimately wins
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 1 ] && ok "a failed copy exits 1" || fail "a failed copy exits 1 -- got $rc"
assert_file "$TREE/$R/Game.srm" "manic-v2" "the copy that could succeed did"
printf '%s' "$out" | grep -q '^  FAILED' \
  && ok "the failure is logged" || { fail "the failure is logged"; note "$out"; }
printf '%s' "$out" | grep -q '^  UNRECORDED' \
  && ok "not recording it is logged" || { fail "not recording it is logged"; note "$out"; }
printf '%s' "$out" | grep -q 'failed: 1' \
  && ok "the summary counts it" || { fail "the summary counts it"; note "$out"; }
if grep -q "$(sha_of "manic-v2")" "$TREE/$STATE"; then
  fail "state did not advance past the failed copy"
  note "state.tsv records the winner's hash despite a copy that never landed"
else
  ok "state did not advance past the failed copy"
fi

# ... and the next run, which is where the damage used to happen. mister's
# directory comes back holding its old bytes. With the state left alone, manic
# and retroarch now both disagree with it, which is a conflict -- loud and
# harmless. With the state wrongly advanced, mister would have been the lone
# mismatch, won, and destroyed manic-v2 everywhere.
rm -f "$TREE/$MG"; mkfile "$TREE/$MG/Game.sav" "v1"
run_bridge --apply >/dev/null; rc=$?
[ "$rc" -eq 2 ] && ok "the run after a failed copy refuses to guess" \
                || fail "the run after a failed copy refuses to guess -- got $rc"
assert_file "$TREE/$M/Game.sav" "manic-v2" "progress survives the failed copy"
assert_file "$TREE/$R/Game.srm" "manic-v2" "progress survives on the side that got it"

section "--seed naming a participant on no mapping row warns"
# gba is manic+retroarch and snes is mister+retroarch, so no single --seed can
# apply to every row. Naming one that applies to none used to surface as a
# conflict message blaming the wrong thing.
new_tree
variant_bridge "seed on no row" '/^  "snes|mister:SNES:sav/d'
mkfile "$TREE/$M/Game.sav" "manic-v1"
out=$(run_bridge --apply --seed=mister); rc=$?
[ "$rc" -eq 0 ] && ok "an inapplicable --seed is not fatal" \
                || fail "an inapplicable --seed is not fatal -- got $rc"
printf '%s' "$out" | grep -q 'appears in no mapping row' \
  && ok "an inapplicable --seed warns" \
  || { fail "an inapplicable --seed warns"; note "$out"; }
assert_file "$TREE/$R/Game.srm" "manic-v1" "the rest of the run is unaffected"

section "three participants still bridge normally"
# The safety rules above must not have cost the generalisation itself: one
# participant holding the file still fans out to the other two, and one of three
# moving against a full set of records still wins.
new_tree; gba_3p "three-way fan-out"
mkfile "$TREE/$M/Game.sav" "v1"
run_bridge --apply >/dev/null
assert_file "$TREE/$R/Game.srm"  "v1" "fan-out reaches retroarch"
assert_file "$TREE/$MG/Game.sav" "v1" "fan-out reaches mister"
mkfile "$TREE/$MG/Game.sav" "mister-v2"
run_bridge --apply >/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "one of three moving is not a conflict" \
                || fail "one of three moving is not a conflict -- got $rc"
assert_file "$TREE/$M/Game.sav" "mister-v2" "the one that moved propagates to manic"
assert_file "$TREE/$R/Game.srm" "mister-v2" "the one that moved propagates to retroarch"

summary "save-bridge"
