#!/bin/bash
#
# Bridges GBA saves between Manic EMU (iPhone, via Synctrain) and RetroArch.
# Both arrive on this box as Syncthing folders.
#
# The two emulators write the same raw GBA flash dump and only disagree on the
# extension -- Manic uses .sav, the libretro mGBA core uses .srm -- so moving a
# save between them is a rename and nothing more. That was verified on a real
# 128K save from both sides: identical size, no header, no footer, and the same
# 14/14/2/2 sector layout, differing only in which save slot was populated.
#
#   ./save-bridge.sh                    show what would happen, change nothing
#   ./save-bridge.sh --apply            actually copy
#   ./save-bridge.sh --apply --seed=manic       first run: Manic wins ties
#   ./save-bridge.sh --apply --seed=retroarch   first run: RetroArch wins ties
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
#
#   Save states are never bridged. Manic EMU documents them as non-portable,
#   and RetroArch's are tied to the exact core build.
#
# WHY IT IS NOT "NEWEST WINS"
#
# Synctrain only syncs while the app is in the foreground, so the phone's copy
# is routinely hours stale while still holding real progress. Comparing mtimes
# would eventually overwrite good data with old data. Instead this records a
# hash of what it last wrote and compares BOTH sides against that record:
#
#   neither side changed  -> nothing to do
#   one side changed      -> copy it across
#   both sides changed    -> copy NEITHER, stash both, log it, exit non-zero
#
# A first run has no record. If both sides hold the same game and the bytes
# differ, that is genuinely unresolvable without being told which one matters,
# which is what --seed is for. It applies only where no record exists.

set -u

# --- configuration ----------------------------------------------------------

SYNC_ROOT="${SYNC_ROOT:-/mnt/user/games/Syncthing}"
MANIC_ROOT="${MANIC_ROOT:-$SYNC_ROOT/ManicEMU}"
# The five per-emulator folders were collapsed into one ("Emulator Saves"), so
# RetroArch is now a subdirectory of it rather than a folder root. Note the
# space in the directory name -- every expansion of this variable is quoted.
RETRO_ROOT="${RETRO_ROOT:-$SYNC_ROOT/Emulator Saves/retroarch}"

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
#   export ST_URL=https://127.0.0.1:8384  ST_KEY=...  ST_FOLDERS="retroarch manicemu"
ST_URL="${ST_URL:-}"
ST_KEY="${ST_KEY:-}"
ST_FOLDERS="${ST_FOLDERS:-}"

# system | manic subdir | manic ext | retroarch subdir | retroarch ext
#
# Only formats proven to be byte-identical between the two belong here. Adding
# a row is a claim that a rename is sufficient -- verify with `cmp` on a real
# save from both emulators before trusting a new one.
MAPPINGS=(
  "gba|gba|sav|saves/mGBA|srm"
)

# Candidates, left commented until each is verified with save-format-check.sh
# against the SAME game saved in BOTH emulators with real progress in each.
# Manic EMU creates a system directory only when you first save in that system,
# so the left-hand names below are informed guesses until they actually exist.
#
#   "snes|snes|srm|saves/Snes9x|srm"     both sides already use .srm, so this
#                                        is likely a plain copy, not a rename
#   "nes|nes|srm|saves/Mesen|srm"
#   "gb|gb|sav|saves/Gambatte|srm"       read the RTC warning first
#
# GB/GBC RTC HAZARD, and it applies to this library specifically: cartridges
# with a real-time clock -- Pokemon Gold, Silver and Crystal, all three of
# which are on the phone -- keep clock state alongside the SRAM, and
# implementations disagree on whether that state is appended to the save file
# and how it is laid out. A save whose size is a clean power of two carries no
# appendix; one that is a power of two plus a small tail does, and renaming
# that into an emulator which does not expect the tail is how a save breaks.
# save-format-check.sh reports exactly that tail. Verify "gb" using an RTC
# game, not a plain cartridge, or the test will pass and the mapping will
# still be wrong for the games you care about.
#
# N64 and NDS are absent on purpose and are not candidates -- see the header.

# --- arguments --------------------------------------------------------------

APPLY=""
SEED=""
for arg in "$@"; do
  case "$arg" in
    --apply)           APPLY=1 ;;
    --seed=manic)      SEED="manic" ;;
    --seed=retroarch)  SEED="retroarch" ;;
    -h|--help)         sed -n '2,45p' "$0"; exit 0 ;;
    *) echo "usage: $0 [--apply] [--seed=manic|--seed=retroarch]" >&2; exit 1 ;;
  esac
done

# --- helpers ----------------------------------------------------------------

STAMP="$(date +%Y%m%d-%H%M%S)"
copied=0; skipped=0; conflicts=0

log() {
  printf '%s\n' "$*"
  [ -n "$APPLY" ] && printf '%s  %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
  return 0
}

hash_of() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }

# Last hash this script wrote for a given system+game, or empty if unknown.
state_get() {
  [ -f "$STATE_FILE" ] || return 0
  awk -F'\t' -v s="$1" -v g="$2" '$1==s && $2==g {print $3; exit}' "$STATE_FILE"
}

state_put() {
  [ -n "$APPLY" ] || return 0
  mkdir -p "$WORK_DIR"
  local tmp="$STATE_FILE.tmp.$$"
  if [ -f "$STATE_FILE" ]; then
    awk -F'\t' -v s="$1" -v g="$2" '!($1==s && $2==g)' "$STATE_FILE" > "$tmp"
  else
    : > "$tmp"
  fi
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$tmp"
  mv "$tmp" "$STATE_FILE"
}

# True when the file has not been modified within QUIET_SECONDS.
is_quiet() {
  local age
  age=$(( $(date +%s) - $(stat -c %Y "$1" 2>/dev/null || echo 0) ))
  [ "$age" -ge "$QUIET_SECONDS" ]
}

backup() {
  [ -n "$APPLY" ] || return 0
  local dest="$BACKUP_DIR/$STAMP/$2"
  mkdir -p "$(dirname "$dest")"
  cp -a "$1" "$dest"
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
transfer() {
  local src="$1" dst="$2" label="$3"
  if [ -z "$APPLY" ]; then
    log "  WOULD COPY  $label"
    return 0
  fi
  [ -e "$dst" ] && backup "$dst" "$(basename "$dst")"
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

# --- preflight --------------------------------------------------------------

# Never let two runs overlap. A cron tick firing while a previous run is still
# working would have two processes deciding about the same files at once.
LOCK_FILE="${LOCK_FILE:-/tmp/save-bridge.lock}"
exec 9>"$LOCK_FILE" || { echo "ERROR: cannot open lock $LOCK_FILE" >&2; exit 1; }
if ! flock -n 9; then
  echo "another save-bridge run is in progress; exiting"
  exit 0
fi

for d in "$MANIC_ROOT" "$RETRO_ROOT"; do
  [ -d "$d" ] || { echo "ERROR: missing folder: $d" >&2; exit 1; }
done
[ -n "$APPLY" ] && mkdir -p "$WORK_DIR" "$BACKUP_DIR" "$CONFLICT_DIR"

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

for row in "${MAPPINGS[@]}"; do
  IFS='|' read -r system m_dir m_ext r_dir r_ext <<< "$row"
  manic="$MANIC_ROOT/$m_dir"
  retro="$RETRO_ROOT/$r_dir"
  echo "[$system]  $manic  <->  $retro"

  # Union of game basenames present on either side.
  games=$(
    { [ -d "$manic" ] && find "$manic" -maxdepth 1 -type f -name "*.$m_ext" -printf '%f\n' | sed "s/\.$m_ext\$//"
      [ -d "$retro" ] && find "$retro" -maxdepth 1 -type f -name "*.$r_ext" -printf '%f\n' | sed "s/\.$r_ext\$//"
    } 2>/dev/null | sort -u
  )
  [ -z "$games" ] && { echo "  (no saves on either side)"; continue; }

  while IFS= read -r game; do
    [ -n "$game" ] || continue
    mf="$manic/$game.$m_ext"
    rf="$retro/$game.$r_ext"
    [ -e "$mf" ] || mf=""
    [ -e "$rf" ] || rf=""

    # Never read a file Syncthing may still be writing.
    for f in "$mf" "$rf"; do
      if [ -n "$f" ] && ! is_quiet "$f"; then
        log "  BUSY        $game (modified <${QUIET_SECONDS}s ago, skipping)"
        skipped=$((skipped+1)); continue 2
      fi
    done

    mh=""; rh=""
    [ -n "$mf" ] && mh=$(hash_of "$mf")
    [ -n "$rf" ] && rh=$(hash_of "$rf")
    last=$(state_get "$system" "$game")

    # Only one side has it: unambiguous, copy it across.
    if [ -n "$mh" ] && [ -z "$rh" ]; then
      transfer "$mf" "$retro/$game.$r_ext" "$game  manic -> retroarch (new)"
      state_put "$system" "$game" "$mh"; copied=$((copied+1)); continue
    fi
    if [ -z "$mh" ] && [ -n "$rh" ]; then
      transfer "$rf" "$manic/$game.$m_ext" "$game  retroarch -> manic (new)"
      state_put "$system" "$game" "$rh"; copied=$((copied+1)); continue
    fi

    [ "$mh" = "$rh" ] && { state_put "$system" "$game" "$mh"; skipped=$((skipped+1)); continue; }

    # Both sides present and differing. The record decides who moved.
    m_changed=1; r_changed=1
    if [ -n "$last" ]; then
      [ "$mh" = "$last" ] && m_changed=0
      [ "$rh" = "$last" ] && r_changed=0
    elif [ -n "$SEED" ]; then
      log "  SEEDING     $game (no record; --seed=$SEED)"
      if [ "$SEED" = "manic" ]; then m_changed=1; r_changed=0; else m_changed=0; r_changed=1; fi
    fi

    if [ "$m_changed" = 1 ] && [ "$r_changed" = 0 ]; then
      transfer "$mf" "$rf" "$game  manic -> retroarch"
      state_put "$system" "$game" "$mh"; copied=$((copied+1))
    elif [ "$m_changed" = 0 ] && [ "$r_changed" = 1 ]; then
      transfer "$rf" "$mf" "$game  retroarch -> manic"
      state_put "$system" "$game" "$rh"; copied=$((copied+1))
    else
      # Both moved since the last bridge, or a first run with no seed given.
      # Guessing here is how save data gets destroyed, so it stops.
      log "  CONFLICT    $game  (both sides changed -- copied neither)"
      if [ -n "$APPLY" ]; then
        d="$CONFLICT_DIR/$STAMP/$system/$game"; mkdir -p "$d"
        cp -a "$mf" "$d/manic.$m_ext"; cp -a "$rf" "$d/retroarch.$r_ext"
        log "              both copies stashed in $d"
      fi
      conflicts=$((conflicts+1))
    fi
  done <<< "$games"
done

echo
echo "copied: $copied   unchanged/skipped: $skipped   conflicts: $conflicts"
[ -z "$APPLY" ] && echo "(dry run -- re-run with --apply to make these changes)"
[ "$conflicts" -gt 0 ] && exit 2
exit 0
