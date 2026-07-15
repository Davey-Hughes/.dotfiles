#!/bin/bash

DOTFDIR=$HOME/.dotfiles

# Where pre-existing files that collide with a symlink target get moved. Created
# lazily on the first conflict; timestamped so repeated runs never clobber a
# previous backup. Kept OUTSIDE the repo so it is never tracked.
BACKUP_DIR="$HOME/.dotfiles-backup/$(date +%Y%m%d-%H%M%S)"
BACKUP_COUNT=0
STOW_FAILED=0

# Echoes the os/ layer(s) to stow for this machine, most-general first so a more
# specific layer can override. Only layers that exist under os/ are stowed.
# SteamOS is Arch-based, so the deck inherits the arch layer (paru, MangoHud).
os_layers() {
  case "$(uname)" in
    Darwin) echo "macos" ;;
    Linux)
      if grep -qi valve /proc/version 2>/dev/null; then
        echo "arch steamdeck"
      elif grep -qi arch /proc/version 2>/dev/null; then
        echo "arch"
      fi
      ;;
  esac
}

# Remove stray symlinks an older install.sh leaked into $HOME root: config
# packages belong under $XDG_CONFIG_HOME, so any NON-dotted $HOME symlink that
# points back into this repo is cruft. Legit home links (~/.zshrc, ~/.claude,
# ...) are dotted and are left untouched. Idempotent.
prune_stray_links() {
  local link name
  for link in "$HOME"/*; do
    [ -L "$link" ] || continue
    name=${link##*/}
    case "$name" in .*) continue ;; esac
    case "$(readlink "$link")" in
      *dotfiles*) rm -v "$link" ;;
    esac
  done
}

# Move a pre-existing target out of the way so stow can link over it. Preserves
# the target's relative path under the backup dir and records that we did it.
backup_target() {
  local abs="$1" rel="$2"
  [ -e "$abs" ] || [ -L "$abs" ] || return 0   # nothing there (already resolved)
  mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
  mv "$abs" "$BACKUP_DIR/$rel"
  echo "  moved $rel -> $BACKUP_DIR/$rel"
  BACKUP_COUNT=$((BACKUP_COUNT + 1))
}

# Ask stow (dry-run) which targets would conflict for this package, then move
# each offending real file/dir into the backup dir. Loops a few times because
# unfolding one conflict can reveal a deeper one. Leaves the filesystem ready
# for a clean, conflict-free stow.
backup_conflicts() {
  local stow_dir="$1" target="$2" pkg="$3"
  local pass rel conflicts
  for pass in 1 2 3 4 5; do
    conflicts=$(stow -n -v2 -d "$stow_dir" -t "$target" "$pkg" 2>&1 | sed -n \
      -e 's/.* over existing target \(.*\) since .*/\1/p' \
      -e 's/.* over existing directory target \(.*\)/\1/p' \
      -e 's/.*existing target is not owned by stow: \(.*\)/\1/p' \
      -e 's/.*existing target is neither a link nor a directory: \(.*\)/\1/p' | sort -u)
    [ -z "$conflicts" ] && return 0
    while IFS= read -r rel; do
      [ -n "$rel" ] && backup_target "$target/$rel" "$rel"
    done <<< "$conflicts"
  done
}

# Back up conflicts, then stow for real and report the true outcome instead of
# swallowing stow's exit status (the old script always printed "complete").
stow_pkg() {
  local stow_dir="$1" target="$2" pkg="$3"
  backup_conflicts "$stow_dir" "$target" "$pkg"
  if ! stow -d "$stow_dir" -t "$target" "$pkg"; then
    echo "ERROR: stow failed for package '$pkg' (dir=$stow_dir target=$target)" >&2
    STOW_FAILED=1
  fi
}

# Pull in git submodules (tpm, zsh-autosuggestions) that .zshrc/.tmux.conf
# source. A plain `git clone` leaves these empty; without them zsh errors on
# every startup and tmux's plugin manager never loads. Non-fatal so an offline
# run still proceeds.
init_submodules() {
  command -v git &>/dev/null || return 0
  [ -f "$DOTFDIR/.gitmodules" ] || return 0
  echo "Initializing git submodules..."
  git -C "$DOTFDIR" submodule update --init --recursive \
    || echo "WARNING: submodule init failed; tpm / zsh-autosuggestions may be missing" >&2
}

symlinks() {
  echo "Creating symlinks..."

  if ! command -v stow &> /dev/null; then
    echo "Stow is not installed. Please install GNU stow first."
    exit 1
  fi

  : "${XDG_CONFIG_HOME:=$HOME/.config}"

  # Clean up stray $HOME-root links from older versions of this script.
  prune_stray_links

  # Create base directories
  mkdir -p "$HOME/.tmux"
  mkdir -p "$XDG_CONFIG_HOME"

  # git_settings() writes the global git config to $XDG_CONFIG_HOME/git/config.
  # Stow folds config/git/ into a single symlink when the target dir does not
  # exist yet, which would send that write inside the repo. Pre-creating it real
  # keeps it unfolded, so `ignore` links in beside a real, untracked `config`.
  mkdir -p "$XDG_CONFIG_HOME/git"

  # Custom cross-linking. ~/.vim is a separate repo (github.com/Davey-Hughes/.vim);
  # NeoVim reads it through this link. On a fresh machine it dangles until that
  # repo is cloned -- clone it if git is available and it is not present yet.
  if [ ! -e "$HOME/.vim" ] && command -v git &>/dev/null; then
    echo "Cloning ~/.vim (NeoVim config)..."
    git clone https://github.com/Davey-Hughes/.vim.git "$HOME/.vim" \
      || echo "WARNING: could not clone ~/.vim; ~/.config/nvim will dangle until it exists" >&2
  fi
  ln -sfn "$HOME/.vim" "$XDG_CONFIG_HOME/nvim"

  # Pre-create config dirs shared between the common layer and an OS layer.
  # GNU stow "folds" a directory into a single symlink when only one package
  # provides it; a second stow dir (the OS layer) then cannot merge into that
  # fold ("existing target is not owned by stow"). Making the shared dir a real
  # directory first keeps it unfolded, so both layers drop per-file symlinks
  # into it (e.g. config/ghostty/config + os/macos/config/ghostty/os.conf).
  for layer in $(os_layers); do
    [ -d "$DOTFDIR/os/$layer/config" ] || continue
    while IFS= read -r dir; do
      rel=${dir#"$DOTFDIR/os/$layer/config/"}
      [ -d "$DOTFDIR/config/$rel" ] && mkdir -p "$XDG_CONFIG_HOME/$rel"
    done < <(find "$DOTFDIR/os/$layer/config" -mindepth 1 -type d)
  done

  pushd "$DOTFDIR" >/dev/null

  # Common configs (all platforms)
  stow_pkg "$DOTFDIR" "$XDG_CONFIG_HOME" config
  stow_pkg "$DOTFDIR" "$HOME" home

  # Platform-specific layers, if present for this OS
  for layer in $(os_layers); do
    [ -d "$DOTFDIR/os/$layer/config" ] && stow_pkg "$DOTFDIR/os/$layer" "$XDG_CONFIG_HOME" config
    [ -d "$DOTFDIR/os/$layer/home" ]   && stow_pkg "$DOTFDIR/os/$layer" "$HOME" home
  done

  popd >/dev/null

  if [ "$BACKUP_COUNT" -gt 0 ]; then
    echo "Moved $BACKUP_COUNT pre-existing file(s) aside into $BACKUP_DIR"
  fi
}

git_settings() {
  echo "Applying git settings..."

  if [ "$(command -v git)" ]; then
    # Keep the global git config under XDG ($XDG_CONFIG_HOME/git/config) instead
    # of ~/.gitconfig. git reads this path natively; GIT_CONFIG_GLOBAL makes
    # --global writes land here too, on fresh machines as well.
    : "${XDG_CONFIG_HOME:=$HOME/.config}"
    mkdir -p "$XDG_CONFIG_HOME/git"
    export GIT_CONFIG_GLOBAL="$XDG_CONFIG_HOME/git/config"
    git config --global init.defaultBranch 'main'
    git config --global push.autoSetupRemote true

    # Deliberately no core.excludesfile: the global ignore list is stowed to
    # $XDG_CONFIG_HOME/git/ignore, which git reads on its own. Pointing
    # excludesfile elsewhere silently shadows that path -- the old setup did
    # exactly that and left a dead ~/.config/git/ignore ignoring nothing.

    # Back the kde-wallpaper filter named by .gitattributes. Repo-local, not
    # --global: .gitattributes only points at it from inside this repo. Filters
    # are not carried by a clone, so without this a fresh machine silently
    # commits the wallpaper paths again -- the exact leak the filter exists to
    # stop. Keep the sed in sync with .gitattributes' comment.
    git -C "$DOTFDIR" config filter.kde-wallpaper.clean "sed -E '/^(Image|SlidePaths)=/d'"
    git -C "$DOTFDIR" config filter.kde-wallpaper.smudge cat
  else
    echo "git not installed" 2>&1
  fi
}

# Install tmux plugins headlessly so a fresh machine never needs an interactive
# `prefix + I`. tpm's bin scripts require TMUX_PLUGIN_MANAGER_PATH, which is only
# set once a tmux server has sourced the config -- so spin up a throwaway
# detached session first, drive tpm, then tear it down. Non-fatal.
tmux_plugins() {
  command -v tmux &>/dev/null || return 0
  local tpm="$HOME/.tmux/plugins/tpm/bin"
  [ -x "$tpm/install_plugins" ] || return 0
  echo "Installing tmux plugins..."
  export TMUX_PLUGIN_MANAGER_PATH="$HOME/.tmux/plugins/"
  tmux new-session -d -s __tpm_bootstrap 2>/dev/null
  "$tpm/install_plugins" || echo "WARNING: tmux plugin install failed" >&2
  tmux kill-session -t __tpm_bootstrap 2>/dev/null
}

install_packages() {
  if [ "$(command -v brew)" ]; then
    KERNEL="linux"
    if [ "$(uname)" = "Darwin" ]; then
      KERNEL="macos"
    fi

    BREWFILE="$DOTFDIR/os/$KERNEL/homebrew/Brewfile"
    if [ -f "$BREWFILE" ]; then
      echo "Installing homebrew packages..."
      brew bundle --no-lock --file "$BREWFILE"
    fi
  fi
}

# Run the core setup tasks automatically
init_submodules
symlinks
git_settings
install_packages
tmux_plugins

if [ "$STOW_FAILED" -ne 0 ]; then
  echo "Dotfiles installation finished WITH ERRORS (see above)." >&2
  exit 1
fi

echo "Dotfiles installation complete!"
