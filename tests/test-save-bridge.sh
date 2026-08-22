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
# would otherwise skip every file this test just created. That is what almost
# every case here needs, and it is why the guard itself went untested for so
# long -- so it is a variable rather than a literal, and exactly one section
# raises it. Raise it inside a command substitution ($(QUIET=... run_bridge)),
# which is a subshell, so no case can leak the setting into the next one.
QUIET=0
run_bridge() {
  SYNC_ROOT="$TREE" \
  MANIC_ROOT="$TREE/ManicEMU" \
  RETRO_ROOT="$TREE/Emulator Saves/retroarch" \
  MISTER_ROOT="$TREE/MiSTer/saves" \
  WORK_DIR="$TREE/.save-bridge" \
  QUIET_SECONDS="$QUIET" \
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

# assert_backup <path tail> <expected content> <label>
#
# The tail is matched in full, not as a substring: the whole question these
# cases ask is which directory a snapshot landed in, and `*/gba/*` says yes to
# .save-bridge/backups/<stamp>/gba/manic/Zelda.sav and to the
# argument-swapped .../manic/gba/Zelda.sav alike.
assert_backup() {
  local f
  f=$(find "$TREE/.save-bridge/backups" -type f -path "*/$1" 2>/dev/null | head -n1)
  if [ -z "$f" ]; then
    fail "$3 -- no snapshot at */$1"
    note "$(find "$TREE/.save-bridge/backups" -type f 2>/dev/null | sed "s#^$TREE/##" | tr '\n' ' ')"
    return
  fi
  assert_file "$f" "$2" "$3"
}

M="ManicEMU/gba"
R="Emulator Saves/retroarch/saves/mGBA"
MG="MiSTer/saves/GBA"
STATE=".save-bridge/state.tsv"

# The snes row's two sides. Only the backup-collision case needs them: it is the
# one test that has to put the SAME game name on two DIFFERENT mapping rows, and
# gba + snes is the pair the stock MAPPINGS already provides.
MS="MiSTer/saves/SNES"
RS="Emulator Saves/retroarch/saves/Snes9x"

section "dry run writes nothing"
new_tree
mkfile "$TREE/$M/Game.sav" "manic-v1"
run_bridge >/dev/null
assert_absent "$TREE/$R/Game.srm" "no copy without --apply"
assert_absent "$TREE/.save-bridge/state.tsv" "no state without --apply"

section "a file Syncthing may still be writing is skipped, not read"
# QUIET_SECONDS is the only thing between the bridge and a half-written save.
# Read mid-write, a file hashes to something no record holds, so it reads as
# "the one that moved" and its truncated bytes are fanned over every other
# participant -- the exact loss this script exists to prevent, arriving on
# exit 0. Every other case in this file flattens the window to 0 to get its work
# done, which left is_quiet, the `busy` break and the BUSY line executed by
# nothing at all.
new_tree
mkfile "$TREE/$M/Game.sav" "manic-v1"
out=$(QUIET=3600 run_bridge --apply); rc=$?
[ "$rc" -eq 0 ] && ok "a file inside the quiet window is not a failed run" \
                || fail "a file inside the quiet window is not a failed run -- got $rc"
printf '%s' "$out" | grep -q '^  BUSY .*Game (manic modified <3600s ago' \
  && ok "the busy participant and the window are both named" \
  || { fail "the busy participant and the window are both named"; note "$out"; }
assert_absent "$TREE/$R/Game.srm" "nothing is copied out of a file that may still be being written"
assert_absent "$TREE/$STATE"      "no record is written for a file that was never read"
printf '%s' "$out" | grep -q 'unchanged/skipped: 1' \
  && ok "the summary counts the busy file as skipped" \
  || { fail "the summary counts the busy file as skipped"; note "$out"; }

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

section "a mapping row naming the same participant twice is fatal"
# Same severity as the undeclared-participant check above, and for a worse
# reason: this one is invisible. pfile and phash are keyed on the participant
# NAME, so a second spec for a name already on the row silently replaces the
# first.
#
# The next edit to MAPPINGS is exactly this shape. The MiSTer keeps three Game
# Boy save directories against RetroArch's single Gambatte, so
#
#   "gb|mister:GAMEBOY:sav|mister:GBC:sav|retroarch:saves/Gambatte:srm"
#
# writes itself. Measured on that exact row with GAMEBOY/Zelda.sav=gameboy-v1
# and GBC/Zelda.sav=gbc-v1, before the preflight learned to refuse it:
#
#   run 1   COPIED Zelda  mister -> manic (new)       <- GBC's bytes, both times
#           COPIED Zelda  mister -> retroarch (new)      GAMEBOY never read
#   run 2   COPIED Zelda  retroarch -> mister         <- logged twice, and both
#           COPIED Zelda  retroarch -> mister            land in GBC
#           conflicts: 0, exit 0
#   after   GAMEBOY/Zelda.sav is still gameboy-v1 -- never compared, never
#           copied, never conflicted, invisible for good
#
# Two participants that disagreed were reported as agreeing, and one of them
# vanished. Three overwrites also produced only two snapshots: both mister specs
# resolve to the same backup path, so the second destroyed the first's -- the
# collision backup() had just been namespaced to close, reopened.
new_tree
variant_bridge "duplicate participant" \
  's#retroarch:saves/mGBA:srm"$#retroarch:saves/mGBA:srm|mister:GBA:sav|mister:GBC:sav"#'
mkfile "$TREE/$M/Game.sav" "manic-v1"
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 1 ] && ok "a duplicated participant exits 1" \
                || fail "a duplicated participant exits 1 -- got $rc"
printf '%s' "$out" | grep -q "names participant 'mister' more than once" \
  && ok "the duplicated participant is named" \
  || { fail "the duplicated participant is named"; note "$out"; }
assert_absent "$TREE/$R/Game.srm" "a rejected table bridges nothing at all"

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

section "two of three changed: nothing copied, every copy stashed"
# A full set of per-participant records exists for all three, and two of them
# disagree with their own. Both moved, so neither is THE one that moved, and
# picking either silently destroys the other's progress. Nothing may be copied,
# and all three copies have to be stashed under the participant they came from
# -- that stash is the only material a human has to merge them by hand.
new_tree; gba_3p "two of three changed"
mkfile "$TREE/$M/Game.sav" "v1"
run_bridge --apply >/dev/null                 # records v1 for all three
mkfile "$TREE/$M/Game.sav"  "manic-v2"
mkfile "$TREE/$MG/Game.sav" "mister-v2"
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 2 ] && ok "two of three changing is a conflict" \
                || fail "two of three changing is a conflict -- got $rc"
assert_file "$TREE/$M/Game.sav"  "manic-v2"  "manic left untouched"
assert_file "$TREE/$R/Game.srm"  "v1"        "retroarch left untouched"
assert_file "$TREE/$MG/Game.sav" "mister-v2" "mister left untouched"
if printf '%s' "$out" | grep -q '^  COPIED'; then
  fail "a two-way conflict copies nothing"; note "$(printf '%s' "$out" | grep '^  COPIED')"
else
  ok "a two-way conflict copies nothing"
fi
printf '%s' "$out" | grep -q '2 participants changed' \
  && ok "the message counts the participants that moved" \
  || { fail "the message counts the participants that moved"; note "$out"; }
d=$(find "$TREE/.save-bridge/conflicts" -type d -name Game | head -n1)
assert_file "$d/manic.sav"     "manic-v2"  "manic's copy is stashed under its own name"
assert_file "$d/retroarch.srm" "v1"        "retroarch's copy is stashed under its own name"
assert_file "$d/mister.sav"    "mister-v2" "mister's copy is stashed under its own name"

section "a conflict whose stash fails says so where the cron can see it"
# save-bridge-cron.sh finds warnings by grepping '^WARNING:', and its exit-2
# alert stated flatly that the versions were stashed. A stash that could not be
# written -- full disk, read-only WORK_DIR, wrong permissions -- was logged
# indented by fourteen spaces, so that grep never matched it and the alert went
# out claiming a snapshot existed at the one moment there was none. That
# snapshot is the thing a human reaches for to sort a conflict out by hand.
#
# The failure is injected by putting a regular FILE where the conflict directory
# belongs, so mkdir -p and cp both fail for root as well as for anyone else --
# the same trick the failed-copy case uses.
new_tree
mkfile "$TREE/$M/Game.sav" "v1"
run_bridge --apply >/dev/null                  # records v1 for both
mkfile "$TREE/$M/Game.sav" "manic-v2"
mkfile "$TREE/$R/Game.srm" "retro-v2"          # both moved: a conflict
rm -rf "${TREE:?}/.save-bridge/conflicts"; : > "$TREE/.save-bridge/conflicts"
out=$(run_bridge --apply); rc=$?
[ "$rc" -eq 2 ] && ok "a conflict with no stash is still a conflict" \
                || fail "a conflict with no stash is still a conflict -- got $rc"
printf '%s' "$out" | grep -q '^WARNING: could NOT stash' \
  && ok "the failed stash is unindented, where the cron's grep reaches it" \
  || { fail "the failed stash is unindented, where the cron's grep reaches it"; note "$out"; }
assert_file "$TREE/$M/Game.sav" "manic-v2" "a failed stash still overwrites nothing on manic"
assert_file "$TREE/$R/Game.srm" "retro-v2" "a failed stash still overwrites nothing on retroarch"

# ... and the same conflict with a writable stash must raise no warning at all,
# or the cron cries wolf every ten minutes for as long as the conflict stands.
new_tree
mkfile "$TREE/$M/Game.sav" "v1"
run_bridge --apply >/dev/null
mkfile "$TREE/$M/Game.sav" "manic-v2"
mkfile "$TREE/$R/Game.srm" "retro-v2"
out=$(run_bridge --apply)
if printf '%s' "$out" | grep -q '^WARNING:'; then
  fail "a stash that worked raises no WARNING"; note "$out"
else
  ok "a stash that worked raises no WARNING"
fi

section "one row's backup must not clobber another's"
# backup() keyed its snapshot on a bare basename under a single per-run stamp,
# so two mapping rows holding the same game name overwrote each other's. gba's
# Zelda.sav (manic) and snes's Zelda.sav (the MiSTer) are different files on
# different participants; whichever row ran second destroyed the first's only
# pre-overwrite copy -- the copy transfer's own comment calls "the only copy of
# them anyone gets to reach for afterwards". Stock MAPPINGS, no variant needed.
new_tree
mkfile "$TREE/$M/Zelda.sav"  "gba-v1"
mkfile "$TREE/$MS/Zelda.sav" "snes-v1"
run_bridge --apply >/dev/null                 # fans both rows out and records them
mkfile "$TREE/$R/Zelda.srm"  "gba-v2"         # retroarch moves on the gba row
mkfile "$TREE/$RS/Zelda.srm" "snes-v2"        # ... and on the snes row too
run_bridge --apply >/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "both rows copy" || fail "both rows copy -- got $rc"
assert_file "$TREE/$M/Zelda.sav"  "gba-v2"  "the gba winner reached manic"
assert_file "$TREE/$MS/Zelda.sav" "snes-v2" "the snes winner reached the MiSTer"

# The bytes each row replaced must BOTH still be reachable afterwards.
saved_gba=""; saved_snes=""
while IFS= read -r f; do
  case "$(cat "$f")" in
    gba-v1)  saved_gba="$f" ;;
    snes-v1) saved_snes="$f" ;;
  esac
done < <(find "$TREE/.save-bridge/backups" -type f -name 'Zelda.sav')
[ -n "$saved_gba" ] && ok "the gba row's replaced save was snapshotted" \
  || { fail "the gba row's replaced save was snapshotted"
       note "$(find "$TREE/.save-bridge/backups" -type f)"; }
[ -n "$saved_snes" ] && ok "the snes row's replaced save was snapshotted" \
  || { fail "the snes row's replaced save was snapshotted"
       note "$(find "$TREE/.save-bridge/backups" -type f)"; }
[ -n "$saved_gba" ] && [ "$saved_gba" != "$saved_snes" ] \
  && ok "the two snapshots are separate files" \
  || fail "the two snapshots are separate files"
# The full tail, not `*/gba/*`. transfer() takes five positional strings
# (src dst system participant label) and `system` and `participant` are adjacent
# same-type arguments -- swap them and backup() files gba's snapshot under
# manic/gba/ instead of gba/manic/, which a substring match on "/gba/" accepts
# just as happily. Naming both components is what pins the order.
case "$saved_gba" in
  */gba/manic/Zelda.sav)
    ok "the gba snapshot is filed under its own row and participant" ;;
  *) fail "the gba snapshot is filed under its own row and participant"
     note "$saved_gba" ;;
esac
case "$saved_snes" in
  */snes/mister/Zelda.sav)
    ok "the snes snapshot is filed under its own row and participant" ;;
  *) fail "the snes snapshot is filed under its own row and participant"
     note "$saved_snes" ;;
esac

section "two participants on ONE row must not clobber each other's backup"
# The case above collides across two ROWS with two DIFFERENT participants, so
# its two snapshots already differ by system AND by participant. Nothing about
# it can require the participant component: drop it, key backups on the system
# alone, and that case still passes in full -- measured.
#
# This is the case that does require it. manic:gba:sav and mister:GBA:sav sit on
# the SAME row and share an extension, so one run replaces two files both named
# Zelda.sav under one system, and only the participant tells them apart.
#
# The assertion is on paths rather than contents, and has to be. A winner exists
# only when every other participant still agrees with its own record, and
# state_put_all writes one hash for all of them -- so the two files being
# replaced are necessarily byte-identical, and the path is the only thing that
# can tell their snapshots apart.
new_tree; gba_3p "same-row backup collision"
mkfile "$TREE/$M/Zelda.sav" "v1"
run_bridge --apply >/dev/null                 # fans v1 out to retroarch and mister
mkfile "$TREE/$R/Zelda.srm" "retro-v2"        # retroarch is the one that moved
run_bridge --apply >/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "one mover on a three-participant row copies" \
                || fail "one mover on a three-participant row copies -- got $rc"
assert_file "$TREE/$M/Zelda.sav"  "retro-v2" "the winner reached manic"
assert_file "$TREE/$MG/Zelda.sav" "retro-v2" "the winner reached mister"
assert_backup "gba/manic/Zelda.sav"  "v1" "manic's replaced save is snapshotted under manic"
assert_backup "gba/mister/Zelda.sav" "v1" "mister's replaced save is snapshotted under mister"

section "a refused seed is named even when it is not the unrecorded one"
# why_more was built only by walking the participants with NO record, so seeding
# one that HAS a record produced a conflict log mentioning the seed zero times
# -- to a reader, indistinguishable from the flag being ignored outright, which
# is the confusion why_more exists to prevent. The same lines are also the only
# place the operator is told which copies the records vouch for, so they have to
# read correctly for a single participant as well as for several.
new_tree
mkfile "$TREE/$M/Game.sav" "v1"
run_bridge --apply >/dev/null                 # records v1 for manic and retroarch
grep -v $'\tretroarch\t' "$TREE/$STATE" > "$TREE/state.keep" && mv "$TREE/state.keep" "$TREE/$STATE"
mkfile "$TREE/$R/Game.srm" "retro-ancient"    # no record of its own, and differs
out=$(run_bridge --apply --seed=manic); rc=$?
[ "$rc" -eq 2 ] && ok "seeding a participant that has a record still conflicts" \
                || fail "seeding a participant that has a record still conflicts -- got $rc"
printf '%s' "$out" | grep -q 'seed=manic was NOT honoured' \
  && ok "the refused seed is named although it is not the unknown one" \
  || { fail "the refused seed is named although it is not the unknown one"; note "$out"; }
printf '%s' "$out" | grep -q 'manic agrees with its own record' \
  && ok "one matched participant reads as one" \
  || { fail "one matched participant reads as one"; note "$out"; }
assert_file "$TREE/$M/Game.sav" "v1"            "manic keeps what its record proves"
assert_file "$TREE/$R/Game.srm" "retro-ancient" "the unrecorded copy stayed put"

summary "save-bridge"
