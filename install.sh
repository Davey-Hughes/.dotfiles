#!/bin/bash

usage() {
  cat <<EOF
usage: $0 [-h] [-z] [-f] [-p] [-v] [-w]
    -h: print this usage statement
    -b: install homebrew
    -z: install oh-my-zsh
    -f: install oh-my-fish
    -p: install packages
    -v: install vim
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

install_ohmyfish() {
  echo "Installing oh-my-fish..."

  curl https://raw.githubusercontent.com/oh-my-fish/oh-my-fish/master/bin/install | fish
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

install_vim() {
  pushd $HOME/.vim
  ./install.sh
  popd
}

symlinks() {
  echo "Creating symlinks..."

  pushd $HOME

  mkdir -p $HOME/.tmux
  mkdir -p $HOME/.config

  ln -sfn $DOTFDIR/.vim $HOME

  # ln -sfn $DOTFDIR/shell/zsh/.zshrc $HOME
  # ln -sfn $DOTFDIR/shell/zsh/davey.zsh-theme $HOME/.oh-my-zsh/themes

  ln -sfn $DOTFDIR/shell/fish/omf $HOME/.config

  ln -sfn $DOTFDIR/term/tmux/.tmux.conf $HOME
  ln -sfn $DOTFDIR/term/tmux/plugins $HOME/.tmux/plugins

  ln -sfn $DOTFDIR/term/kitty $HOME/.config
  ln -sfn $DOTFDIR/term/ghostty $HOME/.config
  ln -sfn $DOTFDIR/term/alacritty $HOME/.config

  ln -sfn $DOTFDIR/readline/.inputrc $HOME
  ln -sfn $DOTFDIR/readline/.editrc $HOME
  ln -sfn $DOTFDIR/postgres/.psqlrc $HOME
  ln -sfn $DOTFDIR/pdb/.pdbrc.py $HOME

  ln -sfn $DOTFDIR/.git_template $HOME

  ln -sfn $DOTFDIR/powerline $HOME/.config

  ln -sfn $DOTFDIR/shell/atuin $HOME/.config

  popd

}

git_settings() {
  echo "Applying git settings..."

  if [ "$(command -v git)" ]; then
    git config --global init.templatedir '~/.git_template'
    git config --global alias.ctags '!.git/hooks/ctags'
    git config --global init.defaultBranch 'main'
    git config --global core.excludesfile '.git_template/.gitignore'
    git config --global push.autoSetupRemote true
  else
    echo "git not installed" 2 &>1
  fi
}

windows_terminal() {
  echo "Copying Windows Terminal config..."

  CDRIVE=/mnt/c

  if [[ $(uname -r) =~ "WSL2" ]]; then
    cp $DOTFDIR/term/windowsTerminal/settings.json $CDRIVE/Users/davey/AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState
  fi
}

update_submodules() {
  :
  # initialize all submodules
  # git submodule update --init --recursive

  # pull newest changes for each submodule, recursively
  # git submodule foreach git checkout master
  # git submodule foreach git pull
}

# assumes dotfiles directory is at this location
DOTFDIR=$HOME/.dotfiles

INSTALL_HOMEBREW=false
INSTALL_OMZSH=false
INSTALL_OMFISH=false
INSTALL_PACKAGES=false
INSTALL_VIM=false

# install windows terminal config
WINDOWS_TERMINAL=false

# these always run by default
CREATE_SYMLINKS=true
GIT_SETTINGS=true

while getopts "hbzfpwv" opt; do
  case "${opt}" in
  b)
    INSTALL_HOMEBREW=true
    ;;
  z)
    INSTALL_OMZSH=true
    ;;
  f)
    INSTALL_OMFISH=true
    ;;
  p)
    INSTALL_PACKAGES=true
    ;;
  v)
    INSTALL_VIM=true
    ;;
  w)
    WINDOWS_TERMINAL=true
    ;;
  h | *)
    usage
    ;;
  esac
done

KERNEL=''
OS=''

case $(uname) in
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
FreeBSD) ;;
esac

if [ "$INSTALL_HOMEBREW" = true ]; then
  install_homebrew
fi

if [ "$INSTALL_OMZSH" = true ]; then
  install_ohmyzsh
fi

if [ "$INSTALL_OMFISH" = true ]; then
  install_ohmyfish
fi

if [ "$INSTALL_PACKAGES" = true ]; then
  install_packages
fi

if [ "$INSTALL_VIM" = true ]; then
  install_vim
fi

if [ "$CREATE_SYMLINKS" = true ]; then
  symlinks
fi

if [ "$GIT_SETTINGS" = true ]; then
  git_settings
fi

update_submodules
