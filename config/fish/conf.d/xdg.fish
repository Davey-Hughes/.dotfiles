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

# redis-cli — REPL history
set -q REDISCLI_HISTFILE; or set -gx REDISCLI_HISTFILE $XDG_DATA_HOME/redis/rediscli_history

# psql — query history
set -q PSQL_HISTORY; or set -gx PSQL_HISTORY $XDG_STATE_HOME/psql/history

# wget — point at an XDG wgetrc that relocates the HSTS database (no env var for it)
set -q WGETRC; or set -gx WGETRC $XDG_CONFIG_HOME/wget/wgetrc

# ipython
set -q IPYTHONDIR; or set -gx IPYTHONDIR $XDG_CONFIG_HOME/ipython

# jupyter — use platformdirs (XDG) instead of ~/.jupyter
set -q JUPYTER_PLATFORM_DIRS; or set -gx JUPYTER_PLATFORM_DIRS 1

# npm — relocate cache and user config out of $HOME
set -q NPM_CONFIG_CACHE; or set -gx NPM_CONFIG_CACHE $XDG_CACHE_HOME/npm
set -q NPM_CONFIG_USERCONFIG; or set -gx NPM_CONFIG_USERCONFIG $XDG_CONFIG_HOME/npm/npmrc

# dotnet
set -q DOTNET_CLI_HOME; or set -gx DOTNET_CLI_HOME $XDG_DATA_HOME/dotnet

# rust — CARGO_HOME (registry cache, bins, credentials) + RUSTUP_HOME (toolchains).
# PATH ($CARGO_HOME/bin) is added by fish_add_path in config.fish.
set -q CARGO_HOME; or set -gx CARGO_HOME $XDG_DATA_HOME/cargo
set -q RUSTUP_HOME; or set -gx RUSTUP_HOME $XDG_DATA_HOME/rustup

# go
set -q GOPATH; or set -gx GOPATH $XDG_DATA_HOME/go
