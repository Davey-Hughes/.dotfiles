#!/bin/bash

DOTFDIR=$HOME/.dotfiles

symlinks() {
  echo "Creating symlinks..."

  if ! command -v stow &> /dev/null; then
    echo "Stow is not installed. Please install GNU stow first."
    exit 1
  fi

  # Create base directories
  mkdir -p $HOME/.tmux
  mkdir -p $HOME/.config

  # Custom cross-linking
  ln -sfn $HOME/.vim $HOME/.config/nvim

  pushd $DOTFDIR >/dev/null
  stow -t $HOME/.config config
  stow -t $HOME home
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
