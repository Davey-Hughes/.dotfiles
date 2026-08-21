#!/bin/bash
#
# Removes host packages that the distrobox or a flatpak now provides.
#
# One-time migration off the old layout, and re-runnable afterwards to catch
# drift -- a stray `sudo pacman -S` on the host shows up here as something to
# clean out. It only ever removes; nothing is installed by this script.
#
#   ./prune-host.sh            show what would be removed, change nothing
#   ./prune-host.sh --apply    actually remove it
#
# Dry-run is the default on purpose: `pacman -Rs` cascades into dependencies,
# and the exact cascade depends on what else this particular Deck has installed.
# Read the list before letting it run.
#
# Run this only AFTER install.sh has completed, so the replacements exist first.

set -u

APPLY=""
case "${1:-}" in
  --apply) APPLY=1 ;;
  "")      ;;
  *)       echo "usage: $0 [--apply]" >&2; exit 1 ;;
esac

# Superseded by the distrobox (container-packages.conf).
#
# NOT listed, deliberately:
#   glibc, linux-api-headers    system packages; the old packages.sh named glibc
#                               but it was always already installed
#   llvm-libs, lib32-llvm-libs  Mesa depends on these; only the `llvm` tooling
#                               package goes. -Rs keeps them, but check the dry
#                               run rather than trusting that.
SUPERSEDED_BY_CONTAINER=(
  gcc
  llvm
  neovim-nightly-bin
  yazi-git
  lazygit
  bottom
  shellcheck-bin
  cloudflared-bin
)

# Superseded by flatpaks.sh. electron34 is not listed because it is obsidian's
# dependency, not an explicit install -- the -s cascade takes it along, and
# naming it here would fail the removal if anything else ever picks it up.
SUPERSEDED_BY_FLATPAK=(
  obsidian
)

# Replaced outright. ghostty took over as the terminal in commit 804ff39; kitty
# has just been sitting on the rootfs since.
REPLACED=(
  kitty
  kitty-shell-integration
)

# What packages.sh puts on the host on purpose, and must survive this script.
#
# This list is not paranoia. `pacman -Rs` also removes packages marked "installed
# as a dependency" once nothing needs them any more, and otf-aurulent-nerd is
# exactly that: yazi-git pulled the Nerd Font in, so removing yazi-git takes the
# glyphs ghostty renders with along with it. Marking these explicit first both
# fixes that and states the truth -- packages.sh installs them by name.
PROTECTED=(
  podman distrobox flatpak
  ghostty fish tmux otf-aurulent-nerd
  starship zoxide eza
  stow paru
)

installed_subset() {
  local pkg
  for pkg in "$@"; do
    pacman -Q "$pkg" >/dev/null 2>&1 && printf '%s\n' "$pkg"
  done
}

mapfile -t targets < <(installed_subset \
  "${SUPERSEDED_BY_CONTAINER[@]}" "${SUPERSEDED_BY_FLATPAK[@]}" "${REPLACED[@]}")

if [ ${#targets[@]} -eq 0 ]; then
  echo "Nothing to prune: none of the superseded packages are installed."
  exit 0
fi

echo "Superseded packages present on the host:"
printf '  %s\n' "${targets[@]}"
echo

mapfile -t protect < <(installed_subset "${PROTECTED[@]}")

if [ -z "$APPLY" ]; then
  # -Rs, not -Rns: pacman rejects --nosave together with --print. The real
  # removal below uses -Rns; the extra n only suppresses .pacsave backups, which
  # a dry run has no opinion about.
  echo "--- dry run: full removal cascade ---"
  cascade=$(pacman -Rs --print "${targets[@]}" 2>&1) || {
    echo "$cascade" >&2
    exit 1
  }
  echo "$cascade"

  # Warn about anything the cascade would sweep that packages.sh wants kept.
  # --apply marks these explicit first, so this is a preview artefact, not a
  # prediction of what --apply does.
  echo
  for pkg in "${protect[@]}"; do
    if grep -q "^${pkg}-[0-9]" <<< "$cascade"; then
      echo "  NOTE: '$pkg' appears above only because it is currently marked a"
      echo "        dependency. --apply re-marks it explicit first, so it stays."
    fi
  done

  echo "Nothing was removed. Re-run with --apply to do it."
  exit 0
fi

if [ ${#protect[@]} -gt 0 ]; then
  echo "==> Marking host packages explicit so the cascade cannot take them"
  sudo pacman -D --asexplicit "${protect[@]}" >/dev/null || exit 1
fi

sudo pacman -Rns "${targets[@]}" || exit 1

# -Rns handles the direct cascade, but packages orphaned earlier (electron34
# after an obsidian removal in a previous run, say) are left behind.
mapfile -t orphans < <(pacman -Qdtq 2>/dev/null)
if [ ${#orphans[@]} -gt 0 ]; then
  echo
  echo "Removing orphaned dependencies:"
  printf '  %s\n' "${orphans[@]}"
  sudo pacman -Rns --noconfirm "${orphans[@]}"
fi

# The protection above is only as good as its list; verify rather than assume.
echo
missing=0
for pkg in "${protect[@]}"; do
  if ! pacman -Q "$pkg" >/dev/null 2>&1; then
    echo "ERROR: '$pkg' was removed despite being protected -- reinstall it with" >&2
    echo "       sudo pacman -S --needed $pkg" >&2
    missing=$((missing + 1))
  fi
done
[ "$missing" -eq 0 ] && echo "All host packages intact."

echo
echo "Prune complete. Rootfs usage now:"
df -h / | tail -1
