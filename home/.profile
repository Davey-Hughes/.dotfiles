# Load shared cross-shell XDG / environment definitions.
xdg_env="${XDG_CONFIG_HOME:-$HOME/.config}/shell/xdg-env.sh"
[ -r "$xdg_env" ] && . "$xdg_env"
unset xdg_env

. "$HOME/.cargo/env"
