#!/bin/bash

DOTFDIR=$DOTFDIR

cd $HOME;

ln -s $DOTFDIR/zsh/.zshrc $HOME/.zshrc
ln -s $DOTFDIR/tmux/.tmux.conf $HOME/.tmux.conf
ln -s $DOTFDIR/readline/.inputrc $HOME/.inputrc
ln -s $DOTFDIR/readline/.editrc $HOME/.editrc
ln -s $DOTFDIR/.git_template $HOME/
ln -s $DOTFDIR/powerline $HOME/.config/
