#!/bin/bash
#
# unraid User Scripts wrapper around save-bridge.sh.
#
# Install: User Scripts -> Add New Script -> name it "save-bridge", then set the
# script body to a single line calling this file, and give it a custom cron of
#
#   */10 * * * *
#
# Ten minutes is deliberate, not conservative. Synctrain only syncs while the
# iPhone app is foregrounded, so saves arrive in bursts when you open it rather
# than continuously. Polling faster than the upstream produces data just burns
# cycles. Ten minutes puts a save on the other device well inside the time it
# takes to pick up a different console.
#
# The Syncthing API key is read from a file, never baked in here -- this script
# lives in a git repo and the key must not.
#
#   mkdir -p /boot/config/save-bridge
#   echo 'YOUR-API-KEY' > /boot/config/save-bridge/apikey
#   chmod 600 /boot/config/save-bridge/apikey
#
# Find the key in the Syncthing UI: Actions -> Settings -> General -> API Key.

set -u

BRIDGE="${BRIDGE:-/mnt/user/games/Syncthing/.save-bridge/bin/save-bridge.sh}"
KEY_FILE="${KEY_FILE:-/boot/config/save-bridge/apikey}"
ST_URL="${ST_URL:-https://127.0.0.1:8384}"
NOTIFY="/usr/local/emhttp/webGui/scripts/notify"

MANIC_PATH="${MANIC_PATH:-/mnt/user/games/Syncthing/ManicEMU}"
RETRO_PATH="${RETRO_PATH:-/mnt/user/games/Syncthing/Emulator Saves}"
MISTER_PATH="${MISTER_PATH:-/mnt/user/games/Syncthing/MiSTer/saves}"

notify() {
  # -e event  -s subject  -d description  -i normal|warning|alert
  [ -x "$NOTIFY" ] && "$NOTIFY" -e "save-bridge" -s "$1" -d "$2" -i "$3"
  return 0
}

[ -x "$BRIDGE" ] || { echo "ERROR: bridge not found or not executable: $BRIDGE" >&2; exit 1; }

if [ ! -r "$KEY_FILE" ]; then
  echo "WARNING: no API key at $KEY_FILE -- running without the idle check." >&2
  echo "         The bridge still honours its quiet window, but folder state is unverified." >&2
  exec "$BRIDGE" --apply
fi
ST_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"

# Resolve folder ids rather than hardcoding them: Synctrain generated the
# ManicEMU id on the phone, and any folder can be re-added with a new one.
#
# Matching is on the LAST PATH COMPONENT, not the full path, because unraid
# runs Syncthing in a container. The daemon reports its own view of the path
# ("/data/Syncthing/ManicEMU", or whatever the volume mapping produces) while
# this script only knows the host path ("/mnt/user/games/Syncthing/ManicEMU").
# Those never compare equal. The basename survives the translation, and label
# is tried as a second chance.
#
# That heuristic is only as good as the basename is distinctive, which is why
# the MiSTer's id is pinned instead -- see below.
#
# Override any of them explicitly if a folder is re-added with a new id:
#   export MANIC_ID=abcde-12345 RETRO_ID=retroarch MISTER_ID=a5fag-scsw7
FOLDER_JSON="$(curl -sk --max-time 15 -H "X-API-Key: $ST_KEY" "$ST_URL/rest/config/folders")"

if [ -z "$FOLDER_JSON" ] || [ "${FOLDER_JSON#\[}" = "$FOLDER_JSON" ]; then
  msg="No folder list from Syncthing at $ST_URL. Check that the API key in $KEY_FILE is current and the URL is right."
  echo "ERROR: $msg" >&2
  [ -n "$FOLDER_JSON" ] && echo "  response was: $(printf '%s' "$FOLDER_JSON" | head -c 200)" >&2
  notify "save-bridge: cannot reach Syncthing" "$msg" "warning"
  exit 1
fi

# unraid's base OS ships neither python nor jq, so the folder list is parsed
# with grep/sed/awk. Syncthing emits "id", "label", "path" and "type" in that
# order for each folder; nested device objects use "deviceID", versioning uses
# "fsPath", and "filesystemType" carries no quote before "type", so none of
# them collides with these patterns.
#
# One key does collide: versioning has a "type" of its own, later in the same
# folder object. Hence `if (type=="")` -- the FIRST type after each id is the
# folder's, and the folder's is never empty, so the second one can never
# displace it. Taking the last would report every folder as "" or "simple".
parse_folders() {
  printf '%s' "$FOLDER_JSON" \
    | grep -oE '"(id|label|path|type)" *: *"[^"]*"' \
    | sed -E 's/^"([a-z]+)" *: *"(.*)"$/\1\t\2/' \
    | awk -F'\t' '
        $1=="id"    { if (seen) print id "\t" label "\t" path "\t" type
                      seen=1; id=$2; label=""; path=""; type="" }
        $1=="label" { label=$2 }
        $1=="path"  { path=$2 }
        $1=="type"  { if (type=="") type=$2 }
        END         { if (seen) print id "\t" label "\t" path "\t" type }'
}

# Every folder Syncthing knows about, for an error message a human has to act on.
dump_folders() {
  parse_folders | awk -F'\t' \
    '{ printf "  id=%-22s type=%-16s label=%-22s path=%s\n", $1, $4, $2, $3 }'
}

folder_id_for() {
  local want base
  want="${1%/}"; base="${want##*/}"
  parse_folders | awk -F'\t' -v want="$want" -v base="$base" '
    { id[NR]=$1; lab[NR]=$2; pth[NR]=$3; n=NR }
    END {
      for (i=1;i<=n;i++) if (pth[i]==want) { print id[i]; exit }   # same-host
      for (i=1;i<=n;i++) {                                          # container
        p=pth[i]; sub(/\/$/,"",p); b=p; sub(/.*\//,"",b)
        if (b==base) { print id[i]; exit }
      }
      for (i=1;i<=n;i++) if (lab[i]==base) { print id[i]; exit }    # by label
    }'
}

MANIC_ID="${MANIC_ID:-$(folder_id_for "$MANIC_PATH")}"
RETRO_ID="${RETRO_ID:-$(folder_id_for "$RETRO_PATH")}"
# Pinned rather than resolved, and it is the only one that is. The MiSTer
# folder's last path component is "saves" -- easily the most generic of the
# three -- and folder_id_for returns the FIRST match in config order without
# ever checking whether a second one exists. Any other folder ending in "saves"
# would take its place silently, and the bridge would then write save files into
# it. The id is stable and known; guessing it is the part that is not. The type
# check below fails loudly if this id ever stops naming a real folder.
MISTER_ID="${MISTER_ID:-a5fag-scsw7}"

if [ -z "$MANIC_ID" ] || [ -z "$RETRO_ID" ] || [ -z "$MISTER_ID" ]; then
  echo "ERROR: could not resolve folder ids (manic='$MANIC_ID' retro='$RETRO_ID' mister='$MISTER_ID')." >&2
  echo "Syncthing reports these folders:" >&2
  dump_folders >&2
  echo "Set MANIC_ID / RETRO_ID / MISTER_ID explicitly from that list if the names do not match." >&2
  notify "save-bridge: cannot resolve folder ids" \
         "Syncthing answered, but a folder did not match by path, basename or label." "warning"
  exit 1
fi
echo "syncthing folders: manic=$MANIC_ID retroarch=$RETRO_ID mister=$MISTER_ID"

# The MiSTer's folder has to be sendreceive, and that is checked rather than
# assumed, because getting it wrong stays silent the whole way through.
#
# Writing into a receiveonly folder does not fail. Syncthing accepts the file,
# files it under "Local Additions" and never sends it, so the MiSTer never sees
# the save at all. The documented cure for local additions is Revert Local
# Changes, which restores the MiSTer's OLDER bytes over the bridge's -- and the
# next run then reads mister as the only participant disagreeing with its own
# record, declares it the one that moved, and fans that stale save back over
# RetroArch. Exit 0, no conflict, no warning, progress gone.
#
# The bridge's own idle check cannot see this: a receiveonly folder full of
# local additions still reports state "idle". FOLDER_JSON is already in hand and
# carries the type, so this costs one more pass over it.
#
# Only the MiSTer's is checked. It is the folder that was created by hand and
# relocated on 2026-08-21, and the only one whose type has ever been anything
# other than sendreceive.
MISTER_TYPE="$(parse_folders | awk -F'\t' -v id="$MISTER_ID" '$1==id { print $4; exit }')"
if [ "$MISTER_TYPE" != "sendreceive" ]; then
  if [ -z "$MISTER_TYPE" ]; then
    subj="save-bridge: MiSTer folder id not found"
    msg="Syncthing has no folder with id '$MISTER_ID'. That id is pinned in save-bridge-cron.sh -- read the current one off the folder in the Syncthing UI and update MISTER_ID."
  else
    subj="save-bridge: MiSTer folder is $MISTER_TYPE, not sendreceive"
    msg="MiSTer saves folder '$MISTER_ID' is type '$MISTER_TYPE'. The bridge writes into it, and anything written to a folder that is not sendreceive stays on this box forever: the MiSTer never receives it, and reverting the local additions restores older saves over newer ones. Set the folder back to Send & Receive before running again."
  fi
  echo "ERROR: $msg" >&2
  echo "Syncthing reports these folders:" >&2
  dump_folders >&2
  notify "$subj" "$msg" "warning"
  exit 1
fi

export ST_URL ST_KEY
export ST_FOLDERS="$MANIC_ID $RETRO_ID $MISTER_ID"

out="$("$BRIDGE" --apply 2>&1)"; rc=$?
printf '%s\n' "$out"

# An unindented "WARNING:" is the bridge's channel for things that did not stop
# the run but that nobody would otherwise ever see. Two produce it:
#
#   a skipped mapping row      its folder does not exist yet. The run still
#                              exits 0 so every OTHER row keeps bridging, so
#                              nothing about it looks wrong from the outside,
#                              and left unwatched that row stays unbridged for
#                              good.
#   a conflict that could not  the alert below tells the operator where the
#   be stashed                 snapshot is. The one case where there is no
#                              snapshot has to arrive alongside it.
#
# Both are emitted unindented in the bridge purely so this grep can reach them;
# the stash failure sits inside a block whose every other line is indented by
# fourteen spaces, and it was invisible here until it came out to the margin.
#
# This is independent of $rc on purpose -- the bridge can skip a row on the same
# run it copies something else or hits a conflict, and all of those combinations
# still need the warning surfaced.
#
# Severity is "warning", the same as the other preflight problems in this
# script, not "alert": nothing has been lost, unlike a CONFLICT, and a skipped
# row is expected for as long as a folder legitimately has not been created yet
# (see Task 6). It is deliberately not deduplicated against the last run --
# nothing else in this script is either -- so it re-fires every ten minutes
# while the folder stays missing. That is noisy, but the alternative is a row
# that can silently stop bridging for good, which is worse.
if printf '%s' "$out" | grep -q '^WARNING:'; then
  n=$(printf '%s' "$out" | grep -c '^WARNING:')
  notify "save-bridge: $n warning(s)" \
         "$(printf '%s' "$out" | grep '^WARNING:')" "warning"
fi

case "$rc" in
  0) # Only speak up when something actually moved; a silent no-op every ten
     # minutes should stay silent.
     if printf '%s' "$out" | grep -q '^  COPIED'; then
       n=$(printf '%s' "$out" | grep -c '^  COPIED')
       notify "save-bridge: $n save(s) bridged" "$(printf '%s' "$out" | grep '^  COPIED')" "normal"
     fi
     ;;
  2) # At least one game could not be decided, so nothing was copied for it.
     #
     # These few lines are the whole of the guidance an operator gets in the
     # middle of an incident, and the two-party reading of this -- "both sides
     # changed, pick one" -- is no longer the likely case. With three
     # participants the common conflict is:
     #
     #   CONFLICT  Game  (no record exists for mister, but manic and retroarch
     #                    agree with their own records ... -- copied none)
     #
     # Nothing moved there. A participant was added, or a mapping row was
     # rewritten, and the newcomer is holding an old save the bridge refuses to
     # let win over saves it can prove are current. There is nothing to merge
     # and nothing to choose between; there is a stale file to get rid of.
     #
     # And the fix is in the LIVE tree, never in the stash. The stash is a
     # snapshot the bridge never reads again -- deleting from it changes nothing
     # about the next run, which reads only the Syncthing folders. "Delete the
     # stale one" pointed at the wrong tree entirely.
     stash="Every participant's copy is stashed under .save-bridge/conflicts/<time>/<system>/<game>/ -- with three participants that is three files, not two."
     if printf '%s' "$out" | grep -q '^WARNING: could NOT stash'; then
       stash="A stash could NOT be written for at least one game -- see the warning above. For those games the files still sitting in the Syncthing folders are the only copies there are."
     fi
     notify "save-bridge: CONFLICT -- no saves copied" \
            "$(printf '%s' "$out" | grep '^  CONFLICT')
$stash
Nothing was copied and nothing was deleted. Fix it in the live tree, not in the stash: decide which participant's save is real, then make the others match it -- overwrite the stale file in its own Syncthing folder, or delete it and let the next run copy the good one back in. The run after that records the agreed bytes and normal bridging resumes." \
            "alert"
     ;;
  *) notify "save-bridge failed (exit $rc)" "$(printf '%s' "$out" | tail -5)" "warning" ;;
esac

exit "$rc"
