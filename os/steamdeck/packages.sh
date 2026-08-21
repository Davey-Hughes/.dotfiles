#!/bin/bash
#
# Packages installed on the HOST rootfs.
#
# Keep this list short and justify every entry. /dev/nvme0n1p4 is 5 GiB, ships
# ~4 GiB full, and is re-imaged by every SteamOS update -- so anything here is
# both scarce and temporary. The bulk of the toolchain lives in the distrobox
# instead (container-packages.conf); GUI apps live in flatpaks.sh.
#
# A package earns a place here only if one of these is true:
#   1. A shell runs it automatically, so a ~100-300 ms container round-trip per
#      call would be intolerable (starship redraws the prompt, zoxide hooks cd,
#      eza is aliased to ls).
#   2. It is what launches the shell, so it cannot itself be in a container
#      (ghostty, fish).
#   3. The container setup depends on it (podman, distrobox).
#   4. ../../install.sh needs it before anything else exists (stow).
#   5. It installs into /opt, which SteamOS bind-mounts to /home -- costing the
#      rootfs nothing (claude-code, eden-nightly-bin).
#
# Requires chaotic-setup.sh to have run first for the chaotic-aur entries.

set -u

# --- container foundation --------------------------------------------------
sudo pacman -S --needed --noconfirm podman distrobox flatpak

# --- terminal and shell: cannot live inside a container ---------------------
sudo pacman -S --needed --noconfirm ghostty fish tmux otf-aurulent-nerd

# --- hot-loop CLI tools: see the split rule in container-packages.conf ------
sudo pacman -S --needed --noconfirm starship zoxide eza

# --- needed by the dotfiles installer itself -------------------------------
sudo pacman -S --needed --noconfirm stow

# --- paru: for the /opt packages below that chaotic-aur does not carry ------
# Note this is `pacman -S`, not a source build: paru is in SteamOS's holo repo.
sudo pacman -S --needed --noconfirm paru

# --- installs into /opt (offloaded to /home), so effectively free ----------
# claude-code comes straight from chaotic-aur, so plain pacman handles it.
sudo pacman -S --needed --noconfirm claude-code

# eden-nightly-bin is AUR-only, so it needs paru. It is a -bin package: paru
# repackages a prebuilt tarball rather than compiling, which is why no build
# toolchain is installed on the host.
paru -S --needed --noconfirm eden-nightly-bin
