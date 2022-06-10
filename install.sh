#!/bin/bash

usage() {
  cat << "EOF"
usage: $0 [-h] [-o] [-p] [-w]
    -h: print this usage statement
    -b: install homebrew
    -o: install ohmyzsh
    -p: install packages
    -w: copy windows terminal config
EOF

  exit 1
}

install_homebrew() {
  echo "Installing homebrew..."

  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
}

install_ohmyzsh() {
  echo "Installing oh-my-zsh..."

   sh -c "$(curl -fsSL https://raw.github.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
}

install_packages() {
  if [ "$(command -v brew)" ]; then
    echo "Installing homebrew packages..."

    brew bundle --no-lock --file $DOTFDIR/homebrew/$KERNEL/Brewfile
  else
    echo "homebrew not installed" 2>&1
  fi

  if [ "$(command -v pip3)" ]; then
    echo "Installing pip3 packages..."

    pip3 install -r $DOTFDIR/pip/requirements.txt
  else
    echo "pip3 not installed" 2>&1
  fi
}

symlinks() {
  echo "Creating symlinks..."

  cd $HOME

  mkdir -p $HOME/.tmux
  mkdir -p $HOME/.config

  ln -sfn $DOTFDIR/tmux/plugins $HOME/.tmux
  ln -sfn $DOTFDIR/zsh/.zshrc $HOME
  ln -sfn $DOTFDIR/tmux/.tmux.conf $HOME
  ln -sfn $DOTFDIR/readline/.inputrc $HOME
  ln -sfn $DOTFDIR/readline/.editrc $HOME
  ln -sfn $DOTFDIR/postgres/.psqlrc $HOME
  ln -sfn $DOTFDIR/.git_template $HOME
  ln -sfn $DOTFDIR/powerline $HOME/.config/
  ln -sfn $DOTFDIR/zsh/davey.zsh-theme $HOME/.oh-my-zsh/themes
  ln -sfn $DOTFDIR/intellij/.ideavimrc $HOME/.ideavimrc
  ln -sfn $DOTFDIR/kitty $HOME/.config
  ln -sfn $DOTFDIR/alacritty $HOME/.config
}

git_settings() {
  echo "Applying git settings..."

  if [ "$(command -v git)" ]; then
    git config --global init.templatedir '~/.git_template'
    git config --global alias.ctags '!.git/hooks/ctags'
    git config --global init.defaultBranch 'main'
  else
    echo "git not installed" 2&>1
  fi
}

windows_terminal() {
  echo "Copying Windows Terminal config..."

  CDRIVE=/mnt/c

  if [[ `uname -r` =~ "WSL2" ]]; then
    cp $DOTFDIR/windowsTerminal/settings.json $CDRIVE/Users/davey/AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState
  fi
}

# assumes dotfiles directory is at this location
DOTFDIR=$HOME/.dotfiles

INSTALL_HOMEBREW=false
INSTALL_OMZSH=false
INSTALL_PACKAGES=false

# install windows terminal config
WINDOWS_TERMINAL=false

# these always run by default
CREATE_SYMLINKS=true
GIT_SETTINGS=true

while getopts "hbopw" opt; do
  case "${opt}" in
    b)
      INSTALL_HOMEBREW=true
      ;;
    o)
      INSTALL_OMZSH=true
      ;;
    p)
      INSTALL_PACKAGES=true
      ;;
    w)
      WINDOWS_TERMINAL=true
      ;;
    h|*)
      usage
      ;;
  esac
done

KERNEL=''
OS=''

case `uname` in
      Darwin)
        KERNEL='macos'
      ;;
      Linux)
        KERNEL='linux'


        version=$(cat /proc/version)
        if [[ $version =~ "arch" ]]; then
          :
        elif [[ $version =~ "Red Hat" ]]; then
          :
        elif [[ $version =~ "WSL2" ]]; then
          :
        else
          :
        fi
      ;;
      FreeBSD)
      ;;
esac

if [ "$INSTALL_HOMEBREW" = true ]; then
  install_homebrew
fi

if [ "$INSTALL_OMZSH" = true ]; then
  install_ohmyzsh
fi

if [ "$INSTALL_PACKAGES" = true ]; then
  install_packages
fi

if [ "$CREATE_SYMLINKS" = true ]; then
  symlinks
fi

if [ "$GIT_SETTINGS" = true ]; then
  git_settings
fi
