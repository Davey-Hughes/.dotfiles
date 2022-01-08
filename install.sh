#!/bin/bash

# assumes dotfiles directory is at this location
DOTFDIR=$HOME/.dotfiles

cd $HOME;

mkdir -p $HOME/.tmux
mkdir -p $HOME/.config

ln -s $DOTFDIR/tmux/plugins $HOME/.tmux
ln -s $DOTFDIR/zsh/.zshrc $HOME
ln -s $DOTFDIR/tmux/.tmux.conf $HOME
ln -s $DOTFDIR/readline/.inputrc $HOME
ln -s $DOTFDIR/readline/.editrc $HOME
ln -s $DOTFDIR/postgres/.psqlrc $HOME
ln -s $DOTFDIR/.git_template $HOME
ln -s $DOTFDIR/powerline $HOME/.config/
ln -s $DOTFDIR/zsh/davey/davey.zsh-theme $HOME/.oh-my-zsh/themes
ln -s $DOTFDIR/intellij/.ideavimrc $HOME/.ideavimrc

# git settings
git config --global init.templatedir '~/.git_template'
git config --global alias.ctags '!.git/hooks/ctags'
