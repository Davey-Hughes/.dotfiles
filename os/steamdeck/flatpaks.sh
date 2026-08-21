#!/bin/bash
#
# GUI applications, installed as flatpaks.
#
# Why flatpak and not pacman: SteamOS bind-mounts /var/lib/flatpak to
# /home/.steamos/offload/var/lib/flatpak, so a flatpak costs the 5 GiB rootfs
# nothing and survives the re-image that every SteamOS update performs. Obsidian
# via pacman cost ~283 MiB of rootfs, because it drags in electron34.
#
# GUI apps are also the one category the distrobox is a poor fit for -- they want
# the host's display, portals and GPU without a wrapper in between.
#
# Idempotent.

set -u

if ! command -v flatpak >/dev/null 2>&1; then
  echo "ERROR: flatpak is not installed. Run packages.sh first." >&2
  exit 1
fi

# --user, so the install lands in ~/.local/share/flatpak (on /home) and needs no
# root. The system-wide remote may already exist; --if-not-exists keeps re-runs
# quiet either way.
flatpak remote-add --user --if-not-exists \
  flathub https://dl.flathub.org/repo/flathub.flatpakrepo

flatpak install --user --noninteractive --or-update flathub \
  md.obsidian.Obsidian

echo "Flatpaks ready."
