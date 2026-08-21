#!/bin/bash
#
# Runs INSIDE the `arch` distrobox. Bootstraps chaotic-aur + paru, installs
# everything in container-packages.conf, and exports the binaries marked there
# into ~/.local/bin on the host.
#
# Not meant to be run directly -- distrobox.sh invokes it through
# `distrobox enter`. It is reachable at the same path inside the container
# because the container shares the host $HOME (see distrobox.ini).
#
# Idempotent: safe to re-run after editing container-packages.conf, which is the
# supported way to add or remove a tool.

set -u

SCRIPTDIR=$(dirname "$(readlink -f "$0")")
CONF="$SCRIPTDIR/container-packages.conf"

# Chaotic-AUR's signing key. Same one the host used to use; it is what makes the
# -bin/-git packages prebuilt downloads instead of half-hour local compiles.
CHAOTIC_KEY=3056513887B78AEB

# Refuse to run on the host. Without this guard a stray invocation would install
# the whole toolchain onto the 5 GiB rootfs -- precisely the thing this repo
# reorganised itself to stop doing.
if [ ! -e /run/.containerenv ] && [ ! -e /.dockerenv ]; then
  echo "ERROR: container-setup.sh must run inside the distrobox, not on the host." >&2
  echo "       Use ./distrobox.sh instead." >&2
  exit 1
fi

if [ ! -r "$CONF" ]; then
  echo "ERROR: cannot read $CONF" >&2
  exit 1
fi

# Emit "package binary,binary" per configured line, comments and blanks dropped.
# Both the install pass and the export pass read the conf through this, so the
# file stays the single source of truth for what exists and what is callable.
parse_conf() {
  sed -e 's/#.*//' -e 's/[[:space:]]//g' "$CONF" |
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      case "$line" in
        *:*) echo "${line%%:*} ${line#*:}" ;;
        *)   echo "$line" ;;
      esac
    done
}

setup_keyring() {
  echo "==> Initialising pacman keyring"
  sudo pacman-key --init
  sudo pacman-key --populate archlinux

  # Already trusted? Then the keyring work below is done. `pacman-key --recv-key`
  # hits a keyserver, so skipping it keeps re-runs offline-friendly.
  if sudo pacman-key --list-keys "$CHAOTIC_KEY" >/dev/null 2>&1; then
    echo "    chaotic-aur key already trusted"
    return 0
  fi

  echo "==> Trusting the chaotic-aur key"
  sudo pacman-key --recv-key "$CHAOTIC_KEY" --keyserver keyserver.ubuntu.com
  sudo pacman-key --lsign-key "$CHAOTIC_KEY"
}

setup_chaotic() {
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

  # Note what is NOT here: `SigLevel = Never`. The host script disabled signature
  # checking globally to get AUR builds through. In a throwaway container there
  # is no reason to, and leaving verification on means a compromised mirror
  # cannot slip a package past us.
  sudo pacman -Sy
}

setup_paru() {
  if command -v paru >/dev/null 2>&1; then
    echo "    paru already installed"
    return 0
  fi
  # chaotic-aur ships paru prebuilt, so this is a download rather than a Rust
  # build -- which is why the keyring work above happens first.
  echo "==> Installing paru"
  sudo pacman -S --noconfirm --needed paru
}

install_packages() {
  local pkgs
  # shellcheck disable=SC2046  # deliberate word splitting: one flat package list
  pkgs=$(parse_conf | awk '{print $1}' | tr '\n' ' ')
  [ -n "${pkgs// /}" ] || { echo "    nothing to install"; return 0; }

  echo "==> Installing: $pkgs"
  # --needed so re-runs are cheap; paru covers both repo and AUR packages, so the
  # conf file does not have to say which is which.
  # shellcheck disable=SC2086
  paru -S --noconfirm --needed $pkgs
}

export_binaries() {
  local pkg bins bin path exported=0 missing=0

  echo "==> Exporting binaries to ~/.local/bin"
  mkdir -p "$HOME/.local/bin"

  while read -r pkg bins; do
    [ -n "${bins:-}" ] || continue
    # Commas to spaces rather than juggling IFS: the loop below already runs
    # inside a `while read`, and reassigning IFS there breaks the outer split.
    for bin in ${bins//,/ }; do
      path=$(command -v "$bin" 2>/dev/null)
      if [ -z "$path" ]; then
        echo "    WARNING: $pkg installed but '$bin' not on PATH; not exported" >&2
        missing=$((missing + 1))
        continue
      fi
      if distrobox-export --bin "$path" --export-path "$HOME/.local/bin" >/dev/null; then
        exported=$((exported + 1))
      else
        echo "    WARNING: failed to export $bin" >&2
        missing=$((missing + 1))
      fi
    done
  done <<< "$(parse_conf)"

  echo "    exported $exported binaries"
  [ "$missing" -gt 0 ] && echo "    $missing binary/binaries could not be exported" >&2
  return 0
}

setup_keyring
setup_chaotic
setup_paru
install_packages
export_binaries

echo "Container setup complete."
