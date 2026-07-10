# XDG Base Directory environment variables (fish).
#
# Mirror of $XDG_CONFIG_HOME/shell/xdg-env.sh (the POSIX copy for bash/zsh).
# KEEP THE TWO IN SYNC when adding or changing a variable.
#
# Spec: https://specifications.freedesktop.org/basedir-spec/latest/

# --- XDG base directories (only set if the session did not already provide them) ---
set -q XDG_CONFIG_HOME; or set -gx XDG_CONFIG_HOME $HOME/.config
set -q XDG_DATA_HOME; or set -gx XDG_DATA_HOME $HOME/.local/share
set -q XDG_STATE_HOME; or set -gx XDG_STATE_HOME $HOME/.local/state
set -q XDG_CACHE_HOME; or set -gx XDG_CACHE_HOME $HOME/.cache

# --- Per-tool XDG redirects (added incrementally, verified one at a time) ---

# node — REPL history
set -q NODE_REPL_HISTORY; or set -gx NODE_REPL_HISTORY $XDG_STATE_HOME/node/repl_history

# python — REPL history (Python 3.13+ honors PYTHON_HISTORY)
set -q PYTHON_HISTORY; or set -gx PYTHON_HISTORY $XDG_STATE_HOME/python/history
