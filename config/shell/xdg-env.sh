# XDG Base Directory environment variables — canonical definitions for POSIX
# shells (bash via ~/.bashrc + ~/.profile, zsh via ~/.zshenv).
#
# Fish has its own copy at $XDG_CONFIG_HOME/fish/conf.d/xdg.fish.
# KEEP THE TWO IN SYNC when adding or changing a variable.
#
# Spec: https://specifications.freedesktop.org/basedir-spec/latest/

# --- XDG base directories (only set if the session did not already provide them) ---
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

# --- Per-tool XDG redirects (added incrementally, verified one at a time) ---

# node — REPL history
export NODE_REPL_HISTORY="${NODE_REPL_HISTORY:-$XDG_STATE_HOME/node/repl_history}"

# python — REPL history (Python 3.13+ honors PYTHON_HISTORY)
export PYTHON_HISTORY="${PYTHON_HISTORY:-$XDG_STATE_HOME/python/history}"
