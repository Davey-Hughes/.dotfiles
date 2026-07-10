#
# ~/.bashrc
#

# If not running interactively, don't do anything
[[ $- != *i* ]] && return

# Load shared cross-shell XDG / environment definitions (also used by zsh).
xdg_env="${XDG_CONFIG_HOME:-$HOME/.config}/shell/xdg-env.sh"
[ -r "$xdg_env" ] && . "$xdg_env"
unset xdg_env

alias ls='ls --color=auto'
alias grep='grep --color=auto'
PS1='[\u@\h \W]\$ '
