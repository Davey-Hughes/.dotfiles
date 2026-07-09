#!/bin/bash
# Give each NFS mount from ./fstab a systemd drop-in that disables the per-mount
# start-limit (StartLimitBurst/StartLimitIntervalSec). Without this, a few fast
# mount failures (e.g. network not up yet at boot, or a mid-session NFS drop)
# trip the limit and systemd marks the .mount AND its .automount 'failed'
# PERMANENTLY -- so the share never remounts on its own until reboot.
# With StartLimitIntervalSec=0 a transient failure just retries on next access.
#
# Run this alongside ./fstab.sh (order doesn't matter -- a drop-in for a unit
# that doesn't exist yet simply takes effect once the unit is generated).
#   ./fstab.sh              # deploy the mounts
#   ./nfs-startlimit-fix.sh # make them self-healing
set -euo pipefail
cd "$(dirname "$0")"

created=0
while read -r src mnt fstype opts rest; do
  case "$src" in ''|\#*) continue ;; esac          # skip blanks / comments
  [ "$fstype" = "nfs" ] || [ "$fstype" = "nfs4" ] || continue

  realmnt="${mnt//\\040/ }"                          # fstab escapes spaces as \040
  unit=$(systemd-escape -p --suffix=mount "$realmnt")
  dir="/etc/systemd/system/${unit}.d"

  sudo mkdir -p "$dir"
  printf '[Unit]\nStartLimitIntervalSec=0\n' | sudo tee "$dir/override.conf" >/dev/null
  echo "wrote $dir/override.conf   ($realmnt)"
  created=$((created + 1))
done < ./fstab

if [ "$created" -eq 0 ]; then
  echo "No NFS entries found in ./fstab" >&2
  exit 1
fi

sudo systemctl daemon-reload
echo "Done: $created drop-in(s) created."
echo "Verify one with: systemctl show home-deck-mnt-daveynet-nfs-games.mount -p StartLimitIntervalUSec  # want 0"
