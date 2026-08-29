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
#
# fakeroot and debugedit are makepkg's problem, not a compiler's. Even a -bin
# package that only repackages a prebuilt tarball still runs through makepkg,
# and makepkg's check_software() refuses to start without them -- so leaving
# them out breaks eden-nightly-bin below even though nothing is compiled.
#
# debugedit is the non-obvious one. /etc/makepkg.conf ships
# OPTIONS=(strip ... debug lto), and the check is gated on `debug` ALONE:
#
#     if check_option "debug" "y"; then ... type -p debugedit ... fi
#
# so a PKGBUILD setting options=(!strip) -- as eden-nightly-bin does -- does
# NOT skip it. The binary is then never actually invoked, because !strip means
# strip.sh (its only caller) never runs. We install a tool purely to satisfy a
# check. 113 KiB; not worth fighting.
#
# base-devel would supply both, but pulling it in just for this would put a
# compiler toolchain on a 5 GiB rootfs; the toolchain lives in the distrobox
# instead (container-packages.conf). These two are the only pieces the host
# genuinely needs.
#
# Both were fixed by hand once before and never written down -- pacman.log's
# first line, 2025-10-02, is `pacman -S --noconfirm fakeroot` -- so the SteamOS
# 3.8 re-image resurrected the same failure. Hence this comment.
sudo pacman -S --needed --noconfirm paru fakeroot debugedit

# --- installs into /opt (offloaded to /home), so effectively free ----------
#
# THE RE-IMAGE PARADOX: /opt surviving is exactly what breaks these installs.
#
# SteamOS bind-mounts /opt to /home/.steamos/offload/opt, so files here outlive
# the A/B re-image -- which is the whole reason these two packages are allowed
# on the host at all. But /var/lib/pacman/local does NOT survive it. So after
# every SteamOS update the files are still on disk while the database has
# forgotten them, pacman sees unowned files, and the transaction aborts:
#
#     error: failed to commit transaction (conflicting files)
#     eden-nightly-bin: /opt/eden-nightly-bin/... exists in filesystem
#
# This hits BOTH packages below, every update, forever. It is not an
# eden-specific problem and it is not something the AUR helper can fix.
#
# --overwrite, scoped to each package's own /opt subtree, re-adopts the orphans.
# Keep the globs narrow: a wider one is a licence to clobber files that another
# package legitimately owns. (pacman matches these without FNM_PATHNAME, so `*`
# spans `/` and covers nested files.)
#
# Deleting the directories first would also work, but an unattended `rm -rf` as
# root is a worse thing to keep in a bootstrap script than a scoped --overwrite.
#
# claude-code comes straight from chaotic-aur, so plain pacman handles it.
sudo pacman -S --needed --noconfirm --overwrite '/opt/claude-code/*' claude-code

# eden-nightly-bin is AUR-only, so it needs paru. It is a -bin package: paru
# repackages a prebuilt tarball rather than compiling, which is why no compiler
# toolchain is installed on the host -- only fakeroot and debugedit, above.
paru -S --needed --noconfirm --overwrite '/opt/eden-nightly-bin/*' eden-nightly-bin
