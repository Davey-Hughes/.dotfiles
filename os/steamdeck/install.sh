#!/bin/bash
#
# Steam Deck bootstrap, in dependency order.
#
# The ordering is not cosmetic: chaotic-setup.sh must configure the repo before
# packages.sh can pull claude-code from it, packages.sh must install podman and
# distrobox before distrobox.sh can use them, and flatpaks.sh needs the flatpak
# binary packages.sh provides.
#
# Most of what this used to install on the rootfs now goes elsewhere -- see the
# header comments in packages.sh, container-packages.conf and flatpaks.sh for
# the split and why it exists.

set -u

# Run from the script's own directory so the relative calls below resolve no
# matter where this was invoked from.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

# SteamOS mounts / read-only. packages.sh still needs this: the terminal, the
# shell and the hot-loop CLI tools genuinely have to be on the host.
sudo steamos-readonly disable

./chaotic-setup.sh || exit 1
./packages.sh      || exit 1
./rustup.sh        || exit 1
./flatpaks.sh      || exit 1
./distrobox.sh     || exit 1

echo "Steam Deck setup complete."
