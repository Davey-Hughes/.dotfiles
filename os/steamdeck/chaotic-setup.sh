#!/bin/bash
#
# Configures the chaotic-aur binary repository on the HOST.
#
# Was paru-setup.sh. It is no longer about paru: paru now ships in SteamOS's own
# holo repo, so it is a plain `pacman -S` line in packages.sh rather than a
# from-source build here. What is left is the repo itself, which the host still
# needs for the handful of packages that install into /opt -- SteamOS
# bind-mounts /opt to /home/.steamos/offload/opt, so those cost the 5 GiB rootfs
# nothing and survive updates.
#
# Everything else that used to come from here now lives in the distrobox; see
# container-packages.conf.
#
# Idempotent.

set -u

# Chaotic-AUR's signing key.
CHAOTIC_KEY=3056513887B78AEB

# NOTE ON SIGNATURE VERIFICATION
#
# The previous version of this script ran
#     sed -i 's/^#*[[:space:]]*SigLevel[[:space:]]*=.*/SigLevel = Never/g' /etc/pacman.conf
# which disabled package signature checking for *every* repo on the machine, to
# get local AUR builds through. Those builds happen in the container now, so the
# host does not need it, and this script no longer sets it.
#
# It does not unset it either: that would change how an already-configured
# machine verifies packages, which is not a side effect an install script should
# have. If the grep below fires, it is left over from the old script -- revert by
# hand with
#     sudo sed -i 's/^SigLevel = Never/SigLevel = Required DatabaseOptional/' /etc/pacman.conf
# then `sudo pacman -Sy` to confirm nothing breaks before relying on it.
if grep -q '^SigLevel = Never' /etc/pacman.conf 2>/dev/null; then
  echo "NOTE: /etc/pacman.conf still has 'SigLevel = Never' from the old setup." >&2
  echo "      See the comment in $(basename "$0") for how to revert it." >&2
fi

echo "==> Initialising pacman keyring"
sudo pacman-key --init
sudo pacman-key --populate archlinux
sudo pacman-key --populate holo

if ! sudo pacman-key --list-keys "$CHAOTIC_KEY" >/dev/null 2>&1; then
  echo "==> Trusting the chaotic-aur key"
  sudo pacman-key --recv-key "$CHAOTIC_KEY" --keyserver keyserver.ubuntu.com
  sudo pacman-key --lsign-key "$CHAOTIC_KEY"
else
  echo "    chaotic-aur key already trusted"
fi

if ! pacman -Q chaotic-keyring >/dev/null 2>&1; then
  echo "==> Installing chaotic-aur keyring and mirrorlist"
  sudo pacman -U --noconfirm \
    'https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-keyring.pkg.tar.zst' \
    'https://cdn-mirror.chaotic.cx/chaotic-aur/chaotic-mirrorlist.pkg.tar.zst'
fi

if ! grep -q '^\[chaotic-aur\]' /etc/pacman.conf; then
  echo "==> Adding chaotic-aur to pacman.conf"
  printf '\n[chaotic-aur]\nInclude = /etc/pacman.d/chaotic-mirrorlist\n' |
    sudo tee -a /etc/pacman.conf >/dev/null
fi

echo "==> Refreshing package databases"
sudo pacman -Sy

echo "chaotic-aur ready."
