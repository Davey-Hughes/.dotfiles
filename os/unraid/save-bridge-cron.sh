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
# Override either explicitly if the heuristics ever pick wrong:
#   export MANIC_ID=abcde-12345 RETRO_ID=retroarch
FOLDER_JSON="$(curl -sk --max-time 15 -H "X-API-Key: $ST_KEY" "$ST_URL/rest/config/folders")"

if [ -z "$FOLDER_JSON" ] || [ "${FOLDER_JSON#\[}" = "$FOLDER_JSON" ]; then
  msg="No folder list from Syncthing at $ST_URL. Check that the API key in $KEY_FILE is current and the URL is right."
  echo "ERROR: $msg" >&2
  [ -n "$FOLDER_JSON" ] && echo "  response was: $(printf '%s' "$FOLDER_JSON" | head -c 200)" >&2
  notify "save-bridge: cannot reach Syncthing" "$msg" "warning"
  exit 1
fi

# unraid's base OS ships neither python nor jq, so the folder list is parsed
# with grep/sed/awk. Syncthing emits "id", "label" and "path" in that order for
# each folder; nested device objects use "deviceID" and versioning uses
# "fsPath", so neither collides with these patterns.
parse_folders() {
  printf '%s' "$FOLDER_JSON" \
    | grep -oE '"(id|label|path)" *: *"[^"]*"' \
    | sed -E 's/^"([a-z]+)" *: *"(.*)"$/\1\t\2/' \
    | awk -F'\t' '
        $1=="id"    { if (seen) print id "\t" label "\t" path; seen=1; id=$2; label=""; path="" }
        $1=="label" { label=$2 }
        $1=="path"  { path=$2 }
        END         { if (seen) print id "\t" label "\t" path }'
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

if [ -z "$MANIC_ID" ] || [ -z "$RETRO_ID" ]; then
  echo "ERROR: could not resolve folder ids (manic='$MANIC_ID' retro='$RETRO_ID')." >&2
  echo "Syncthing reports these folders:" >&2
  parse_folders | awk -F'\t' '{ printf "  id=%-22s label=%-22s path=%s\n", $1, $2, $3 }' >&2
  echo "Set MANIC_ID / RETRO_ID explicitly from that list if the names do not match." >&2
  notify "save-bridge: cannot resolve folder ids" \
         "Syncthing answered, but neither folder matched by path, basename or label." "warning"
  exit 1
fi
echo "syncthing folders: manic=$MANIC_ID retroarch=$RETRO_ID"

export ST_URL ST_KEY
export ST_FOLDERS="$MANIC_ID $RETRO_ID"

out="$("$BRIDGE" --apply 2>&1)"; rc=$?
printf '%s\n' "$out"

case "$rc" in
  0) # Only speak up when something actually moved; a silent no-op every ten
     # minutes should stay silent.
     if printf '%s' "$out" | grep -q '^  COPIED'; then
       n=$(printf '%s' "$out" | grep -c '^  COPIED')
       notify "save-bridge: $n save(s) bridged" "$(printf '%s' "$out" | grep '^  COPIED')" "normal"
     fi
     ;;
  2) # Both sides changed since the last run. Nothing was copied, both versions
     # are stashed, and it needs a human to say which one is real.
     notify "save-bridge: CONFLICT -- no saves copied" \
            "$(printf '%s' "$out" | grep '^  CONFLICT')
Both versions are stashed under .save-bridge/conflicts/. Resolve manually, then delete the stale one." \
            "alert"
     ;;
  *) notify "save-bridge failed (exit $rc)" "$(printf '%s' "$out" | tail -5)" "warning" ;;
esac

exit "$rc"
