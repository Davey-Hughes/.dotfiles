#!/bin/bash
#
# Host-side driver for the Arch container that holds the CLI toolchain.
#
#   ./distrobox.sh              create (if absent) and sync packages
#   ./distrobox.sh --replace    tear the container down and rebuild from scratch
#
# Two steps, in this order and not the other: distrobox assemble builds the
# container from distrobox.ini, then container-setup.sh runs inside it to
# install container-packages.conf and export the binaries. assemble's own
# `additional_packages`/`exported_bins` keys cannot do the second step, because
# they fire before an AUR helper exists in the container.
#
# Re-run this after editing container-packages.conf; it is idempotent.

set -u

SCRIPTDIR=$(dirname "$(readlink -f "$0")")
BOX=arch
REPLACE=""

case "${1:-}" in
  --replace|-R) REPLACE="--replace" ;;
  "")           ;;
  *)            echo "usage: $0 [--replace]" >&2; exit 1 ;;
esac

for cmd in podman distrobox; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd is not installed. Run packages.sh first." >&2
    exit 1
  fi
done

# Rootless podman needs a subuid/subgid range for the user; without one the
# container fails to start with an opaque "lchown ... invalid argument". SteamOS
# ships this configured, but a reimaged Deck or a different user will not have it.
if ! grep -q "^${USER}:" /etc/subuid 2>/dev/null; then
  echo "ERROR: no /etc/subuid range for '$USER' -- rootless podman cannot run." >&2
  echo "       Fix: sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USER" >&2
  exit 1
fi

echo "==> Assembling '$BOX' from distrobox.ini"
# shellcheck disable=SC2086  # $REPLACE is a single optional flag, intentionally unquoted
if ! distrobox assemble create $REPLACE --file "$SCRIPTDIR/distrobox.ini"; then
  echo "ERROR: distrobox assemble failed." >&2
  exit 1
fi

echo "==> Running container-setup.sh inside '$BOX'"
# Reachable at the same path inside the container because $HOME is shared.
if ! distrobox enter "$BOX" -- "$SCRIPTDIR/container-setup.sh"; then
  echo "ERROR: container setup failed." >&2
  exit 1
fi

cat <<'MSG'

Container toolchain ready.

  Exported binaries are in ~/.local/bin (on PATH via config/fish/steamdeck.fish).
  Shell into the container with:  distrobox enter arch
  Add or remove a tool by editing container-packages.conf, then re-running this.

MSG
