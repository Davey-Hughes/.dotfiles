#!/bin/bash

DOTFDIR=$HOME/.dotfiles

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

symlinks() {
  echo "Creating symlinks..."

  if ! command -v stow &> /dev/null; then
    echo "Stow is not installed. Please install GNU stow first."
    exit 1
  fi

  : "${XDG_CONFIG_HOME:=$HOME/.config}"

  # Create base directories
  mkdir -p "$HOME/.tmux"
  mkdir -p "$XDG_CONFIG_HOME"

  # Custom cross-linking
  ln -sfn "$HOME/.vim" "$XDG_CONFIG_HOME/nvim"

  pushd "$DOTFDIR" >/dev/null

  # Common configs (all platforms)
  stow -t "$XDG_CONFIG_HOME" config
  stow -t "$HOME" home

  # Platform-specific layers, if present for this OS
  for layer in $(os_layers); do
    [ -d "$DOTFDIR/os/$layer/config" ] && stow -d "$DOTFDIR/os/$layer" -t "$XDG_CONFIG_HOME" config
    [ -d "$DOTFDIR/os/$layer/home" ]   && stow -d "$DOTFDIR/os/$layer" -t "$HOME" home
  done

  popd >/dev/null
}

git_settings() {
  echo "Applying git settings..."

  if [ "$(command -v git)" ]; then
    git config --global init.templatedir "$HOME/.git_template"
    git config --global alias.ctags '!.git/hooks/ctags'
    git config --global init.defaultBranch 'main'
    git config --global core.excludesfile "$HOME/.git_template/.gitignore"
    git config --global push.autoSetupRemote true
  else
    echo "git not installed" 2>&1
  fi
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
symlinks
git_settings
install_packages

echo "Dotfiles installation complete!"
