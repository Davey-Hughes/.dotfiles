#!/bin/bash
#
# Bridges emulator saves between Manic EMU (iPhone, via Synctrain), RetroArch
# and the MiSTer FPGA. All of them arrive on this box as Syncthing folders.
#
# A row in MAPPINGS is a claim that the participants on it write the same raw
# save and disagree on nothing but the extension, so moving a save between them
# is a rename and nothing more. GBA was verified on a real 128K save from both
# sides: identical size, no header, no footer, and the same 14/14/2/2 sector
# layout, differing only in which save slot was populated.
#
#   ./save-bridge.sh                    show what would happen, change nothing
#   ./save-bridge.sh --apply            actually copy
#   ./save-bridge.sh --apply --seed=<participant>   first run: that one wins ties
#                                                   (manic | retroarch | mister)
#
# Exit status: 0 nothing went wrong, 1 at least one copy failed, 2 at least one
# game was left alone because the evidence was ambiguous. 2 outranks 1.
#
# Dry-run is the default on purpose. This writes save files, and a wrong copy
# costs somebody their progress.
#
# WHAT IT DELIBERATELY WILL NOT TOUCH
#
#   N64  RetroArch packs EEPROM + SRAM + FlashRAM + 4 mempaks into a single
#        296960-byte .srm. Nothing else reads that layout. It needs splitting,
#        not renaming, so it is not in MAPPINGS.
#   NDS  DeSmuME's .dsv appends a "|-DESMUME SAVE-|" footer past the save data,
#        so a rename produces a file melonDS will reject or misread.
#   FDS  The MiSTer's NES directory holds Famicom Disk System writable disk
#        images under the same .sav extension as raw SRAM, at 131072 bytes
#        against SRAM's 8192. A MAPPINGS row applies to every file in a
#        directory, so it cannot exclude them by name -- the size guards below
#        do that instead.
#
#   Save states are never bridged. Manic EMU documents them as non-portable,
#   and RetroArch's are tied to the exact core build.
#
# SIZE GUARDS
#
# A rename cannot be correct when the two files are not the same length, so
# before any decision is made about a game:
#
#   participants disagree on size   skip the game. That is a format
#                                   incompatibility, never a conflict to settle
#                                   by picking a winner.
#   the row carries sizes=N[,N...]  skip any game whose file is not one of those
#                                   lengths on every participant holding it.
#
# Both are permanent, expected conditions -- an FDS disk image will never become
# bridgeable -- so each logs INDENTED and counts as skipped. Deliberately not
# "WARNING:", which save-bridge-cron.sh turns into an unraid notification every
# ten minutes for as long as the file sits there.
#
# WHY IT IS NOT "NEWEST WINS"
#
# Synctrain only syncs while the app is in the foreground, so the phone's copy
# is routinely hours stale while still holding real progress. Comparing mtimes
# would eventually overwrite good data with old data. Instead this records a
# hash of what it last wrote and compares EVERY participant against that record:
#
#   agrees with its record  -> it did not move
#   differs from its record -> it moved
#   has NO record           -> unknown. Never counted as having moved: a
#                              participant that was only just added looks
#                              exactly like one holding real progress, and the
#                              newcomer's ancient copy must not be allowed to
#                              win and overwrite everybody else.
#
# Exactly one moved and nothing is unknown -> copy that one onto every other
# participant. Anything else -- nobody identifiable, two or more moved, or one
# participant is unknown while any other has a record at all -- copies NOTHING,
# stashes every copy, logs it and exits 2. Guessing here is how save data gets
# destroyed.
#
# A first run has no record for anybody. If two or more participants hold the
# same game and the bytes differ, that is genuinely unresolvable without being
# told which participant matters, which is what --seed is for. It resolves ONLY
# the case where NOT ONE participant present has a record. The moment any of
# them does, that record is evidence -- it either proves that participant moved,
# or proves it did not -- and evidence outranks being told, so the seed is
# refused and the run stops instead.

set -u

# --- configuration ----------------------------------------------------------

SYNC_ROOT="${SYNC_ROOT:-/mnt/user/games/Syncthing}"
MANIC_ROOT="${MANIC_ROOT:-$SYNC_ROOT/ManicEMU}"
# The five per-emulator folders were collapsed into one ("Emulator Saves"), so
# RetroArch is now a subdirectory of it rather than a folder root. Note the
# space in the directory name -- every expansion of this variable is quoted.
RETRO_ROOT="${RETRO_ROOT:-$SYNC_ROOT/Emulator Saves/retroarch}"
# The MiSTer's saves folder, relocated from the mirrors share on 2026-08-21.
MISTER_ROOT="${MISTER_ROOT:-$SYNC_ROOT/MiSTer/saves}"

# State and backups live beside the folders, never inside them -- anything
# inside a folder root would sync itself back out to every device.
WORK_DIR="${WORK_DIR:-$SYNC_ROOT/.save-bridge}"
STATE_FILE="$WORK_DIR/state.tsv"
BACKUP_DIR="$WORK_DIR/backups"
CONFLICT_DIR="$WORK_DIR/conflicts"
LOG_FILE="$WORK_DIR/save-bridge.log"

# A file still being written by Syncthing must not be read. Skip anything
# touched more recently than this.
QUIET_SECONDS="${QUIET_SECONDS:-120}"

# Optional. With both set, folder state is checked via the REST API and the run
# aborts unless every folder is idle. Without them, only the mtime check above
# applies -- weaker, but it still refuses to touch actively-changing files.
# List EVERY folder a MAPPINGS row reads. One left out is one whose sync state
# is never checked, which is the case this guard exists to catch.
#   export ST_URL=https://127.0.0.1:8384  ST_KEY=...  \
#          ST_FOLDERS="retroarch manicemu mister"
ST_URL="${ST_URL:-}"
ST_KEY="${ST_KEY:-}"
ST_FOLDERS="${ST_FOLDERS:-}"

# name | root
#
# A participant is one place saves live. Adding one here does nothing on its
# own -- it has to appear in a MAPPINGS row before any file is looked at.
PARTICIPANTS=(
  "manic|$MANIC_ROOT"
  "retroarch|$RETRO_ROOT"
  "mister|$MISTER_ROOT"
)

# Root directory for a participant name, or empty if it is not declared.
#
# It sits here rather than with the other helpers because --seed validation
# below calls it, and bash only knows a function once the line defining it has
# actually run -- a definition further down the file would not exist yet.
participant_root() { # name
  local row
  for row in "${PARTICIPANTS[@]}"; do
    [ "${row%%|*}" = "$1" ] && { printf '%s' "${row#*|}"; return 0; }
  done
  return 1
}

# system | [sizes=N[,N...]] | participant:subdir:ext | participant:subdir:ext | ...
#
# Two or more participants per row, each named at most once -- a participant
# repeated on one row is rejected by the preflight, which explains why. Only
# formats proven byte-identical between every participant on the row belong
# here. Adding a row is a claim that a rename is sufficient -- verify with
# save-format-check.sh on a real save from each side before trusting a new one.
#
# The optional sizes= field is a CONSTRAINT, not a participant: it restricts the
# row to files of exactly those byte lengths and may sit anywhere after the
# system name. It exists because a row applies to every file in a directory,
# and one directory can hold more than one format under one extension -- see the
# nes evidence below. A row without it accepts any length, exactly as before.
# The participants-disagree-on-size guard applies to every row either way.
MAPPINGS=(
  "gba|manic:gba:sav|retroarch:saves/mGBA:srm"
  "snes|mister:SNES:sav|retroarch:saves/Snes9x:srm"
  "nes|sizes=8192|mister:NES:sav|retroarch:saves/FCEUmm:srm"
)

# Verified 2026-08-21 with save-format-check.sh, against three games present on
# both the MiSTer and in RetroArch's Snes9x directory under identical names --
# ALttP-msu-Deluxe, Retroid, The Legend of Zelda SNES. All six files are 8192
# bytes, all classify as raw dumps with no footer, and all three pairs return
# RENAME LIKELY SAFE. That is what put the snes row above.
#
# Candidates, left commented until each is verified the same way against the
# SAME game saved in BOTH places with real progress in each:
#
#   "snes|manic:snes:srm|retroarch:saves/Snes9x:srm"   both sides already use
#                                                      .srm, so likely a plain
#                                                      copy, not a rename
#
# Verified 2026-08-22 with save-format-check.sh, and the reason the nes row
# carries sizes=. RetroArch does now hold an NES save -- FCEUmm's `Legend of
# Zelda, The (USA) (Rev 1).srm`, 8192 bytes; there is no saves/Mesen directory,
# FCEUmm is the core in use here -- and checked against the MiSTer's `Zelda no
# Densetsu 1 - The Hyrule Fantasy (Japan).sav`, also 8192 bytes, that pair
# returns RENAME LIKELY SAFE.
#
# That verifies ONE size and says nothing about the others. The MiSTer's nine
# NES saves are three different formats sharing the .sav extension:
#
#     8192  raw SRAM. Byte-compatible with FCEUmm's .srm, and the only size
#           there is a verified pair for.
#    32768  8 KB of real save data followed by 24 KB of inert padding -- the
#           mapper's declared PRG-RAM size, not data. Six of the nine are this.
#           FCEUmm writes 8192, so a rename hands it a file four times the
#           length it expects, and nothing here establishes what it does with
#           one.
#   131072  NOT SRAM at all. Famicom Disk System writable DISK IMAGES -- `Zelda
#           no Densetsu` and `Metroid (Japan)` -- carrying real data well past
#           the 8 KB mark. Renaming one to .srm hands FCEUmm a disk image as
#           battery backup, which is the exact class of mistake the
#           verification rule exists to prevent.
#
# The row cannot exclude the other two by name, because it applies to every file
# in the directory. sizes=8192 is what excludes them. Widening it means
# verifying the wider size against a real pair the same way first.
#
# GB/GBC was attempted and is blocked on two independent grounds:
#
#   1. RTC. `Pokemon - Crystal Version (USA).sav` on the MiSTer is 33280 bytes
#      = 32768 + 512. That 512-byte tail is an RTC appendix; Gambatte keeps RTC
#      state in a separate .rtc file rather than appended to the SRAM, so a
#      rename hands it 512 bytes it does not expect. Gold, Silver and Crystal
#      are all affected, and they are the GB games that matter here.
#   2. Cardinality. The MiSTer keeps THREE Game Boy save directories -- GAMEBOY,
#      GBC and SGB -- against RetroArch's single Gambatte, and the same game
#      appears in two of them at once (Link's Awakening DX in GBC and SGB;
#      Pokemon Yellow in GAMEBOY and GBC). Which one is authoritative is not
#      answerable from the filesystem. The shortcut that suggests itself --
#      naming mister twice on one row -- is refused by the preflight, and the
#      comment there says what it would otherwise have destroyed.
#
# N64 and NDS remain absent on purpose and are not candidates -- see the header.

# --- arguments --------------------------------------------------------------

APPLY=""
SEED=""
for arg in "$@"; do
  case "$arg" in
    --apply)    APPLY=1 ;;
    --seed=*)   SEED="${arg#--seed=}"
                if ! participant_root "$SEED" >/dev/null; then
                  echo "unknown participant for --seed: $SEED" >&2
                  echo "known participants: ${PARTICIPANTS[*]%%|*}" >&2
                  exit 1
                fi ;;
                # The header block ends at the first blank line in the file, so
                # match that rather than a line number -- the number has already
                # needed bumping once and silently truncates the help when it is
                # wrong.
    -h|--help)  sed -n '2,/^$/p' "$0"; exit 0 ;;
    *) echo "usage: $0 [--apply] [--seed=<participant>]" >&2; exit 1 ;;
  esac
done

# --- helpers ----------------------------------------------------------------

STAMP="$(date +%Y%m%d-%H%M%S)"
copied=0; skipped=0; conflicts=0; failed=0

log() {
  printf '%s\n' "$*"
  [ -n "$APPLY" ] && printf '%s  %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
  return 0
}

# Empties for a file it cannot read -- mode 000, most likely -- which is the
# case the size guards below actually have to tolerate: an empty hash matches
# no other participant's hash, so a game where one copy is unreadable falls
# out of the agreed branch on its own and is decided, or refused, on whatever
# evidence remains.
hash_of() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

# Unlike hash_of, this needs no read permission on the file -- only search
# permission on the parent directory, which the caller's preceding `[ -e "$f" ]`
# has already proved. A file unreadable to this user still reports its real
# size here. size_of only comes back empty on a narrower race: the file is
# deleted between that `-e` test and this stat. The ${psize[$p]:-unreadable}
# fallbacks in the size guards below exist for that race -- near-unreachable,
# but correct if it ever fires.
size_of() { stat -c %s "$1" 2>/dev/null; }

# Last hash this script wrote for a given system+game+participant, or empty.
#
# state.tsv v1 was `system<TAB>game<TAB>hash` -- a single hash, because there
# were only ever two participants and they agreed by construction. That is the
# entire content of a v1 row: "the two participants on this row both held this
# hash". It says nothing whatsoever about a third.
#
# So the v1 fallback fires ONLY when the row being decided still has exactly two
# participants. Be exact about what that proves: n==2 is the row's ARITY, not
# its IDENTITY. Rewrite a two-participant row from manic|retroarch to
# manic|mister and n is still 2, so mister is handed a hash it never wrote,
# mismatches it, is judged the one that moved, and overwrites manic. A v1 row
# carries no participant names at all, so there is nothing in it to check the
# membership against -- that hole is not closable from a v1 row alone. The arity
# check narrows it to a row whose membership was edited while a v1 row for that
# system+game still existed, and no further.
#
# Where the fallback does not fire, state_get reports "no record", which lands
# the game in the unknown-participant branch that refuses to guess. v1 rows are
# replaced the first time state_put_all rewrites that system+game.
state_get() { # system game participant participant-count-on-the-row
  [ -f "$STATE_FILE" ] || return 0
  awk -F'\t' -v s="$1" -v g="$2" -v p="$3" -v n="$4" '
    $1==s && $2==g {
      if (NF>=4 && $3==p) { print $4; exit }
      if (NF==3 && n==2)  { print $3; exit }
    }' "$STATE_FILE"
}

# Record one hash for every participant of a system+game.
#
# This only ever runs after all participants have been made identical -- either
# they already agreed, or one was just copied onto the others -- so a single
# hash is correct for all of them. Writing them together is also what keeps a
# partial update from stranding one participant with a stale record and making
# it look "changed" on the next run.
state_put_all() { # system game hash participant...
  local system="$1" game="$2" hash="$3"; shift 3
  [ -n "$APPLY" ] || return 0
  # Never record a blank hash. hash_of returns empty for a file it cannot read,
  # and two unreadable-but-present copies compare equal, so the agreed branch
  # would reach here with nothing to write. A blank fourth field reads back as
  # "no record", which fails safe -- the next run conflicts rather than deciding
  # wrongly -- but it also silently discards the real records these rows replace.
  # Writing nothing keeps them.
  [ -n "$hash" ] || return 0
  mkdir -p "$WORK_DIR"
  local tmp="$STATE_FILE.tmp.$$" p
  if [ -f "$STATE_FILE" ]; then
    awk -F'\t' -v s="$system" -v g="$game" '!($1==s && $2==g)' "$STATE_FILE" > "$tmp"
  else
    : > "$tmp"
  fi
  for p in "$@"; do
    printf '%s\t%s\t%s\t%s\n' "$system" "$game" "$p" "$hash" >> "$tmp"
  done
  mv "$tmp" "$STATE_FILE"
}

# True when the file has not been modified within QUIET_SECONDS.
is_quiet() {
  local age
  age=$(( $(date +%s) - $(stat -c %Y "$1" 2>/dev/null || echo 0) ))
  [ "$age" -ge "$QUIET_SECONDS" ]
}

# Snapshot a file about to be overwritten. Returns non-zero if the snapshot did
# not happen, and the caller must treat that as a reason not to overwrite: the
# whole value of this copy is that it exists before the destination is lost, so
# proceeding without it trades a recoverable mistake for an unrecoverable one.
#
# The path is namespaced by system and participant, exactly as the conflict
# stash is, and for the same reason. $STAMP is computed once per run, so a
# snapshot keyed on a bare basename is overwritten by the next file of that
# name the same run touches -- gba's Zelda.sav replaced by snes's Zelda.sav,
# leaving one file where two saves were destroyed. Two participants on ONE row
# can collide the same way whenever they share an extension (manic:gba:sav and
# mister:GBA:sav both write Zelda.sav), so the system alone is not enough.
#
# It is not only the snapshot that is lost. Where the colliding second copy
# cannot overwrite the first -- saves at mode 0444, a non-root run -- cp fails,
# backup reports failure, and transfer then refuses a copy that was perfectly
# legitimate. Distinct paths fix both.
backup() { # file system participant
  [ -n "$APPLY" ] || return 0
  local dest
  dest="$BACKUP_DIR/$STAMP/$2/$3/$(basename "$1")"
  mkdir -p "$(dirname "$dest")" && cp -a "$1" "$dest"
}

# Copy preserving mtime, but only after stashing whatever is being replaced.
#
# The write goes to a temp file in the SAME directory and is then renamed.
# rename(2) is atomic within a filesystem, so anything reading the destination
# -- Syncthing's scanner, or RetroArch itself -- sees either the old file or
# the complete new one, never a partially-written save. A plain `cp` straight
# onto the destination would leave a window where a scan could pick up a
# truncated file and replicate it to every device.
#
# The copy is hash-verified before the rename, so a short read or a full disk
# fails loudly instead of silently publishing a corrupt save.
transfer() { # src dst system participant label
  local src="$1" dst="$2" system="$3" participant="$4" label="$5"
  if [ -z "$APPLY" ]; then
    log "  WOULD COPY  $label"
    return 0
  fi
  # No snapshot, no overwrite. The bytes at $dst are about to stop existing, and
  # this is the only copy of them anyone gets to reach for afterwards -- which
  # is why the snapshot is filed under the system and participant it came from
  # rather than its bare name. See backup().
  if [ -e "$dst" ] && ! backup "$dst" "$system" "$participant"; then
    log "  FAILED      $label (could not back up the file being replaced -- nothing written)"
    return 1
  fi
  mkdir -p "$(dirname "$dst")"

  local tmp
  tmp="$(dirname "$dst")/.save-bridge.tmp.$$"
  if ! cp -a "$src" "$tmp"; then
    log "  FAILED      $label (copy failed)"; rm -f "$tmp"; return 1
  fi
  if [ "$(hash_of "$tmp")" != "$(hash_of "$src")" ]; then
    log "  FAILED      $label (hash mismatch after copy -- nothing written)"
    rm -f "$tmp"; return 1
  fi
  if ! mv -f "$tmp" "$dst"; then
    log "  FAILED      $label (rename failed)"; rm -f "$tmp"; return 1
  fi
  log "  COPIED      $label"
}

# What to do about a game where at least one transfer failed.
#
# state_put_all must NOT run. Recording the winner's hash for participants that
# never received it is how a half-finished fan-out becomes silent data loss: the
# participant that missed the copy is then the only one disagreeing with its own
# record, so the NEXT run reads it as the one that moved and propagates its
# stale bytes back over everybody, destroying the progress that just won.
#
# Leaving the record alone instead means the next run sees the same evidence
# this one did, minus whatever succeeded -- usually a conflict it will refuse to
# resolve, which is loud and harmless, and exactly the outcome to want here.
record_nothing() { # game
  log "  UNRECORDED  $1 (a copy failed, so the winner's hash was NOT written)"
  log "              the next run re-decides from the same records this one read"
  failed=$((failed+1))
}

# --- preflight --------------------------------------------------------------

# Never let two runs overlap. A cron tick firing while a previous run is still
# working would have two processes deciding about the same files at once.
LOCK_FILE="${LOCK_FILE:-/tmp/save-bridge.lock}"
exec 9>"$LOCK_FILE" || { echo "ERROR: cannot open lock $LOCK_FILE" >&2; exit 1; }
if ! flock -n 9; then
  echo "another save-bridge run is in progress; exiting"
  exit 0
fi

# Made before the mapping walk below, so that walk's warnings can reach the log
# file and not just the terminal output a cron job discards.
[ -n "$APPLY" ] && mkdir -p "$WORK_DIR" "$BACKUP_DIR" "$CONFLICT_DIR"

# Only the participants that actually appear in MAPPINGS need to exist. A
# declared-but-unused participant is not an error -- that is how a new one gets
# staged before its row is added.
#
# The three ways a row can fail here are deliberately NOT the same severity:
#
#   undeclared participant   a config error. The name resolves to nothing, so
#                            nothing about the run can be trusted. Stop.
#   participant named twice  also a config error, and a far quieter one -- see
#                            below. Stop, for the same reason.
#   folder does not exist    a staging state, and a routine one -- the MiSTer's
#                            saves folder did not exist for days after its row
#                            was written. Aborting the whole run there would
#                            stop bridging every OTHER row too, so a healthy
#                            GBA sync would quietly die while the ten-minute
#                            cron mailed a failure forever. Skip that row only.
#
# The duplicate check is not hypothetical. The obvious way to reach the MiSTer's
# three Game Boy directories is to put two of them on one row --
#
#   "gb|mister:GAMEBOY:sav|mister:GBC:sav|retroarch:saves/Gambatte:srm"
#
# -- and everything the main loop records per game (pfile, phash) is keyed on
# the participant NAME, so the second spec silently replaces the first. GAMEBOY
# is then never read at all: its save is never compared, never copied and never
# conflicted, while GBC's bytes are reported under the name "mister". Two
# directories that disagree are announced as agreeing. Both specs also resolve
# to the same backup path, reopening the collision backup() was just namespaced
# to close. Nothing about that run looks wrong from the outside, which is
# exactly why it stops here instead of warning.
#
# Three GB directories against one Gambatte need three participants and three
# rows, or a decision about which one is authoritative -- not one row naming the
# same participant twice.
ACTIVE_MAPPINGS=()
for row in "${MAPPINGS[@]}"; do
  IFS='|' read -r -a _fields <<< "$row"
  _missing=""; _seen=""; _sizes=""
  for spec in "${_fields[@]:1}"; do
    # A sizes= field constrains the row; it is not a participant spec, so it is
    # taken out before the participant checks below ever see it.
    #
    # A malformed value is fatal for the same reason an undeclared participant
    # is: it is silent. `sizes=8192 `, `sizes=8k` or `sizes=08192` matches no
    # file's length at all -- stat -c %s never emits a leading zero -- so every
    # game on the row is skipped and the run looks exactly like a row whose
    # directories happen to be empty. A bare `sizes=0` is not this: zero-byte
    # saves are real and 0 has no leading zero to strip, so it stays legal on
    # its own or alongside other lengths. Two sizes= fields are refused
    # rather than merged for the same reason the duplicate-participant check
    # refuses two specs for one name -- the second would quietly replace the
    # first, and a narrower allowlist than the author wrote is a row that
    # silently bridges nothing.
    case "$spec" in
      sizes=*)
        if [ -n "$_sizes" ]; then
          echo "ERROR: mapping row '${_fields[0]}' carries more than one sizes= field." >&2
          echo "       A row may have at most one. Combine them into a single" >&2
          echo "       comma-separated list, e.g. sizes=8192,32768." >&2
          exit 1
        fi
        _sizes="${spec#sizes=}"
        # 0[0-9]* catches a leading zero at the start of the string, and
        # *,0[0-9]* catches one leading any later entry -- each requires a
        # SECOND digit after the zero, so a bare 0 (start, middle, or end of
        # the list) never matches either arm and stays legal.
        case "$_sizes" in
          ''|*[!0-9,]*|,*|*,|*,,*|0[0-9]*|*,0[0-9]*)
            echo "ERROR: mapping row '${_fields[0]}' has a malformed sizes= field: '$spec'" >&2
            echo "       Expected sizes=<bytes>[,<bytes>...] -- digits and commas only," >&2
            echo "       no spaces, no empty entries, and no leading zeros (a bare 0 is" >&2
            echo "       fine). For example: sizes=8192,32768" >&2
            exit 1 ;;
        esac
        continue ;;
    esac
    p="${spec%%:*}"
    d="$(participant_root "$p")" || { echo "ERROR: mapping names an undeclared participant: $p" >&2; exit 1; }
    # The quotes inside the pattern are what keep $p literal: an unquoted
    # expansion in a case pattern is matched as a glob.
    case " $_seen " in
      *" $p "*)
        echo "ERROR: mapping row '${_fields[0]}' names participant '$p' more than once." >&2
        echo "       A participant may appear at most once per row. Everything this" >&2
        echo "       script records per game is keyed on the participant name, so the" >&2
        echo "       second spec would replace the first: one of the two directories" >&2
        echo "       would never be read, and the run would look entirely normal." >&2
        echo "       Split them across rows, or declare them as separate participants." >&2
        exit 1 ;;
    esac
    _seen="$_seen $p"
    [ -d "$d" ] || _missing="$_missing $p ($d)"
  done
  if [ -n "$_missing" ]; then
    log "WARNING: skipping mapping row '${_fields[0]}' -- no such folder:$_missing"
    continue
  fi
  ACTIVE_MAPPINGS+=("$row")
done
unset _fields _missing _seen _sizes

# --seed can only break a tie on a row that names it. Naming a participant no
# row mentions is almost always a typo or a half-finished MAPPINGS edit, and it
# used to produce a misleading conflict message instead of an explanation. It
# harms nothing on its own, so it warns rather than stopping the run.
if [ -n "$SEED" ]; then
  seed_on_a_row=""
  for row in "${MAPPINGS[@]}"; do
    IFS='|' read -r -a _fields <<< "$row"
    for spec in "${_fields[@]:1}"; do
      # sizes= is a constraint, not a participant -- same rule as the walk above
      # and as the main loop, kept identical at all three parse sites so the
      # field can never be read as a participant name at any of them.
      case "$spec" in sizes=*) continue ;; esac
      [ "${spec%%:*}" = "$SEED" ] && { seed_on_a_row=1; break 2; }
    done
  done
  unset _fields
  [ -n "$seed_on_a_row" ] || \
    log "WARNING: --seed=$SEED names a participant that appears in no mapping row -- it can break no ties"
fi

if [ -n "$ST_URL" ] && [ -n "$ST_KEY" ] && [ -n "$ST_FOLDERS" ]; then
  for f in $ST_FOLDERS; do
    state=$(curl -sk -H "X-API-Key: $ST_KEY" "$ST_URL/rest/db/status?folder=$f" \
            | sed -n 's/.*"state" *: *"\([^"]*\)".*/\1/p')
    if [ "$state" != "idle" ]; then
      echo "ERROR: syncthing folder '$f' is '${state:-unreachable}', not idle. Try again shortly." >&2
      exit 1
    fi
  done
  echo "syncthing: all folders idle"
else
  echo "syncthing: API not configured, relying on the ${QUIET_SECONDS}s quiet window only"
fi

[ -z "$APPLY" ] && echo "DRY RUN -- nothing will be written. Re-run with --apply." && echo

# --- main -------------------------------------------------------------------

for row in ${ACTIVE_MAPPINGS+"${ACTIVE_MAPPINGS[@]}"}; do
  IFS='|' read -r -a fields <<< "$row"
  system="${fields[0]}"
  specs=("${fields[@]:1}")

  # Resolve each participant's directory and extension once per system, and pick
  # the row's size allowlist out of the same fields. The preflight has already
  # proved any sizes= field well-formed and at most one per row, so this only
  # has to read it.
  names=(); dirs=(); exts=(); row_sizes=""
  for spec in "${specs[@]}"; do
    case "$spec" in sizes=*) row_sizes="${spec#sizes=}"; continue ;; esac
    IFS=':' read -r p_name p_sub p_ext <<< "$spec"
    names+=("$p_name")
    dirs+=("$(participant_root "$p_name")/$p_sub")
    exts+=("$p_ext")
  done

  printf '[%s]' "$system"
  [ -n "$row_sizes" ] && printf '  sizes=%s' "$row_sizes"
  for i in "${!names[@]}"; do printf '  %s=%s' "${names[$i]}" "${dirs[$i]}"; done
  printf '\n'

  # Union of game basenames across every participant.
  games=$(
    for i in "${!names[@]}"; do
      [ -d "${dirs[$i]}" ] && find "${dirs[$i]}" -maxdepth 1 -type f -name "*.${exts[$i]}" \
        -printf '%f\n' | sed "s/\.${exts[$i]}\$//"
    done 2>/dev/null | sort -u
  )
  [ -z "$games" ] && { echo "  (no saves on any participant)"; continue; }

  while IFS= read -r game; do
    [ -n "$game" ] || continue

    # Cleared here, not at the end: several branches below `continue`, so there
    # is no single end-of-iteration point to clear them at. `declare -A x=()`
    # does empty an existing array on this bash, and the `=()` is what makes
    # these two BOUND rather than merely declared -- an unbound array would make
    # a later ${#pfile[@]} a fatal error under `set -u`. The `unset` in front is
    # belt-and-braces, not a requirement.
    unset pfile phash psize; declare -A pfile=() phash=() psize=()
    present=(); absent=(); busy=""
    for i in "${!names[@]}"; do
      f="${dirs[$i]}/$game.${exts[$i]}"
      if [ -e "$f" ]; then
        # Never read a file Syncthing may still be writing.
        if ! is_quiet "$f"; then busy="${names[$i]}"; break; fi
        pfile["${names[$i]}"]="$f"
        phash["${names[$i]}"]=$(hash_of "$f")
        psize["${names[$i]}"]=$(size_of "$f")
        present+=("${names[$i]}")
      else
        pfile["${names[$i]}"]="$f"
        absent+=("${names[$i]}")
      fi
    done
    if [ -n "$busy" ]; then
      log "  BUSY        $game (${busy} modified <${QUIET_SECONDS}s ago, skipping)"
      skipped=$((skipped+1)); continue
    fi

    # Cannot happen -- the game name came from the union of files that exist --
    # but ${present[0]} under `set -u` would be a crash rather than a message.
    if [ ${#present[@]} -eq 0 ]; then
      log "  SKIPPED     $game (no readable copy on any participant)"
      skipped=$((skipped+1)); continue
    fi

    # --- size guards --------------------------------------------------------
    #
    # Both of these run BEFORE the agreed/changed/unknown decision below, and
    # they have to. Two participants holding different lengths reach that
    # decision as an ordinary hash disagreement, where the records then pick a
    # "winner" and fan it out -- which is how a Famicom Disk System disk image
    # gets renamed over somebody's SRAM on exit 0. The premise of a MAPPINGS row
    # is that bridging is a rename, and a rename cannot be correct when the two
    # files are not the same length. That is settled before anyone asks who
    # moved.
    #
    # Neither is a conflict, and neither stashes anything. A conflict is a
    # decision this script refused to make and a human can make instead; these
    # are formats that do not correspond, so there is nothing to decide and no
    # snapshot to reach for. They count as skipped and leave the exit status
    # alone.
    #
    # Both log INDENTED. save-bridge-cron.sh notifies on '^WARNING:' every ten
    # minutes, and these fire on permanent conditions -- an FDS image will never
    # become bridgeable -- so an unindented line would be a notification every
    # ten minutes forever. '^WARNING:' stays for structural problems like a
    # missing participant folder.

    # The row's own allowlist first, where it has one. A row carrying sizes= has
    # already declared which lengths are the format it was verified for, so a
    # file outside that list is outside the row's scope entirely -- reporting it
    # as "participants disagree" would name a symptom of something the
    # configuration already ruled out, and point the reader at the wrong file.
    if [ -n "$row_sizes" ]; then
      unlisted=""
      for p in "${present[@]}"; do
        case ",$row_sizes," in
          *",${psize[$p]},"*) ;;
          *) unlisted="$unlisted $p=${psize[$p]:-unreadable}" ;;
        esac
      done
      if [ -n "$unlisted" ]; then
        log "  SIZE        $game (row is limited to sizes=$row_sizes, and$unlisted -- copied none)"
        skipped=$((skipped+1)); continue
      fi
    fi

    # And the unconditional one, which applies to every row whether it carries a
    # sizes= field or not.
    if [ ${#present[@]} -gt 1 ]; then
      size_first="${psize[${present[0]}]}"
      sizes_differ=""; size_list=""
      for p in "${present[@]}"; do
        [ "${psize[$p]}" = "$size_first" ] || sizes_differ=1
        size_list="$size_list $p=${psize[$p]:-unreadable}"
      done
      if [ -n "$sizes_differ" ]; then
        log "  SIZE        $game (participants hold different lengths:$size_list -- a rename cannot be correct, copied none)"
        skipped=$((skipped+1)); continue
      fi
    fi

    # Do every participant present already agree? Then the only question left
    # is whether anyone is missing the file.
    agreed=1; first="${present[0]}"
    for p in "${present[@]}"; do
      [ "${phash[$p]}" = "${phash[$first]}" ] || { agreed=0; break; }
    done

    if [ "$agreed" = 1 ]; then
      if [ ${#absent[@]} -eq 0 ]; then
        state_put_all "$system" "$game" "${phash[$first]}" "${names[@]}"
        skipped=$((skipped+1)); continue
      fi
      # any_copied is tracked separately from all_copied because a partial
      # fan-out still WROTE files, and a summary reading "copied: 0" after files
      # changed on disk is a summary that lies about what the run did.
      all_copied=1; any_copied=0
      for p in "${absent[@]}"; do
        if transfer "${pfile[$first]}" "${pfile[$p]}" "$system" "$p" "$game  $first -> $p (new)"; then
          any_copied=1
        else
          all_copied=0
        fi
      done
      if [ "$all_copied" = 1 ]; then
        state_put_all "$system" "$game" "${phash[$first]}" "${names[@]}"
        copied=$((copied+1))
      else
        [ "$any_copied" = 1 ] && copied=$((copied+1))
        record_nothing "$game"
      fi
      continue
    fi

    # Participants disagree. The record decides who moved -- never the mtime,
    # because Synctrain leaves the phone's copy hours stale while it still
    # holds real progress.
    #
    # Three outcomes per participant, and the third is the one that matters:
    # "no record" is NOT evidence of movement. A participant that has never been
    # bridged is byte-for-byte indistinguishable from one holding new progress,
    # so treating the two the same lets a newly-added participant's ancient copy
    # win and overwrite everyone.
    #
    # `matched` -- present, has a record, and agrees with it -- is collected
    # rather than left implied, because when a seed has to be refused the reason
    # is precisely WHICH participants have records, and the message has to name
    # them.
    changed=(); unknown=(); matched=()
    for p in "${present[@]}"; do
      last=$(state_get "$system" "$game" "$p" "${#names[@]}")
      if [ -z "$last" ]; then
        unknown+=("$p")
      elif [ "${phash[$p]}" != "$last" ]; then
        changed+=("$p")
      else
        matched+=("$p")
      fi
    done

    winner=""; why=""; why_more=""
    if [ ${#changed[@]} -gt 0 ] && [ ${#unknown[@]} -gt 0 ]; then
      # Mixed evidence. --seed deliberately does not rescue this: it exists to
      # break a first-run tie, and once something has demonstrably moved this is
      # not a first run for that game. Overriding here would let a stale
      # newcomer beat progress the record can actually prove.
      why="${changed[*]} moved since the last bridge, but there is no record at all for ${unknown[*]}"
    elif [ ${#unknown[@]} -gt 0 ] && [ ${#unknown[@]} -ne ${#present[@]} ]; then
      # Some participant is unknown, but NOT all of them: the rest hold records
      # and agree with them, which is positive proof those participants did not
      # move. A seed says "no one can be identified, so use this one"; here
      # someone CAN be, and a record beats being told. Honouring the seed here
      # is the exact shape of the data loss this script exists to avoid -- the
      # newcomer's ancient copy fanned out over saves proven current, silently
      # and on exit 0.
      #
      # These two lines are the whole of the operator's guidance mid-conflict,
      # so they have to read correctly for one matched participant as well as
      # several.
      if [ ${#matched[@]} -eq 1 ]; then
        m_agree="agrees with its own record, which proves it did not move"
        m_have="already has a record"
      else
        m_agree="agree with their own records, which proves they did not move"
        m_have="already have records"
      fi
      why="no record exists for ${unknown[*]}, but ${matched[*]} $m_agree"
      for p in "${unknown[@]}"; do
        [ "$p" = "$SEED" ] && \
          why_more="--seed=$SEED was NOT honoured: a seed settles a first run only, and this is not one -- ${matched[*]} $m_have"
      done
    elif [ ${#unknown[@]} -gt 0 ]; then
      # Every participant present is unknown, so nothing is on record to be
      # overridden. This is the genuine first run --seed is for.
      for p in "${unknown[@]}"; do
        [ "$p" = "$SEED" ] && winner="$SEED"
      done
      if [ -n "$winner" ]; then
        log "  SEEDING     $game (no record for any of ${unknown[*]}; --seed=$SEED)"
      else
        why="no record exists for ${unknown[*]}, and --seed names none of them"
      fi
    elif [ ${#changed[@]} -eq 1 ]; then
      winner="${changed[0]}"
    elif [ ${#changed[@]} -eq 0 ]; then
      why="every participant matches its own record, so no participant could be identified as the one that moved"
    else
      why="${#changed[@]} participants changed since the last bridge (${changed[*]})"
    fi

    # A seed that was given, could have applied, and was not used has to be
    # named. The branch above only names it when the seed happens to be one of
    # the participants WITHOUT a record; seed a participant that has one and the
    # conflict log contained the word "seed" nowhere at all, which reads exactly
    # like the flag having been forgotten -- the confusion why_more exists to
    # prevent. Skipped for the all-unknown branch, whose own message already
    # says what became of the seed, and for a seed holding no copy of this game,
    # which could not have broken the tie whatever the records said.
    if [ -z "$winner" ] && [ -n "$SEED" ] && [ -z "$why_more" ] \
       && [ ${#unknown[@]} -ne ${#present[@]} ]; then
      for p in "${present[@]}"; do
        [ "$p" = "$SEED" ] && \
          why_more="--seed=$SEED was NOT honoured: a seed settles a first run only -- one where no participant present has a record -- and this is not one"
      done
    fi

    if [ -n "$winner" ]; then
      all_copied=1; any_copied=0
      for p in "${names[@]}"; do
        [ "$p" = "$winner" ] && continue
        if transfer "${pfile[$winner]}" "${pfile[$p]}" "$system" "$p" "$game  $winner -> $p"; then
          any_copied=1
        else
          all_copied=0
        fi
      done
      if [ "$all_copied" = 1 ]; then
        state_put_all "$system" "$game" "${phash[$winner]}" "${names[@]}"
        copied=$((copied+1))
      else
        [ "$any_copied" = 1 ] && copied=$((copied+1))
        record_nothing "$game"
      fi
    else
      # Guessing here is how save data gets destroyed, so it stops.
      log "  CONFLICT    $game  ($why -- copied none)"
      [ -n "$why_more" ] && log "              $why_more"
      if [ -n "$APPLY" ]; then
        d="$CONFLICT_DIR/$STAMP/$system/$game"; mkdir -p "$d"
        stash_failed=""
        for i in "${!names[@]}"; do
          p="${names[$i]}"
          [ -e "${pfile[$p]}" ] || continue
          cp -a "${pfile[$p]}" "$d/$p.${exts[$i]}" || stash_failed="$stash_failed $p"
        done
        # A conflict is the worst possible moment to be told the snapshot is
        # safe when it is not -- it is the copy a human reaches for to sort the
        # conflict out by hand.
        #
        # So this one line is unindented, unlike everything else inside a
        # conflict block. save-bridge-cron.sh turns bridge output into unraid
        # notifications by grepping '^WARNING:', which an indented line cannot
        # match, and its conflict alert is what tells the operator where the
        # snapshot is. Left indented, the run that has no snapshot would be the
        # run that most confidently claims one.
        if [ -n "$stash_failed" ]; then
          log "WARNING: could NOT stash$stash_failed into $d"
          log "              (the originals are untouched, but there is no snapshot of them)"
        else
          log "              every copy stashed in $d"
        fi
      fi
      conflicts=$((conflicts+1))
    fi
  done <<< "$games"
done

echo
echo "copied: $copied   unchanged/skipped: $skipped   conflicts: $conflicts   failed: $failed"
[ -z "$APPLY" ] && echo "(dry run -- re-run with --apply to make these changes)"
# Conflicts keep precedence over failures: a conflict is a decision this script
# refused to make and a human has to, which outranks a copy that can be retried.
[ "$conflicts" -gt 0 ] && exit 2
[ "$failed" -gt 0 ] && exit 1
exit 0
