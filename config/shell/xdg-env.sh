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

# redis-cli — REPL history
export REDISCLI_HISTFILE="${REDISCLI_HISTFILE:-$XDG_DATA_HOME/redis/rediscli_history}"

# psql — query history
export PSQL_HISTORY="${PSQL_HISTORY:-$XDG_STATE_HOME/psql/history}"

# wget — point at an XDG wgetrc that relocates the HSTS database (no env var for it)
export WGETRC="${WGETRC:-$XDG_CONFIG_HOME/wget/wgetrc}"

# ipython
export IPYTHONDIR="${IPYTHONDIR:-$XDG_CONFIG_HOME/ipython}"

# jupyter — use platformdirs (XDG) instead of ~/.jupyter
export JUPYTER_PLATFORM_DIRS="${JUPYTER_PLATFORM_DIRS:-1}"

# npm — relocate cache and user config out of $HOME
export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-$XDG_CACHE_HOME/npm}"
export NPM_CONFIG_USERCONFIG="${NPM_CONFIG_USERCONFIG:-$XDG_CONFIG_HOME/npm/npmrc}"

# dotnet
export DOTNET_CLI_HOME="${DOTNET_CLI_HOME:-$XDG_DATA_HOME/dotnet}"

# rust — CARGO_HOME (registry cache, bins, credentials) + RUSTUP_HOME (toolchains).
# Replaces sourcing ~/.cargo/env, whose generated content hardcodes $HOME/.cargo/bin.
export CARGO_HOME="${CARGO_HOME:-$XDG_DATA_HOME/cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-$XDG_DATA_HOME/rustup}"
case ":$PATH:" in *":$CARGO_HOME/bin:"*) ;; *) export PATH="$CARGO_HOME/bin:$PATH" ;; esac

# go
export GOPATH="${GOPATH:-$XDG_DATA_HOME/go}"
case ":$PATH:" in *":$GOPATH/bin:"*) ;; *) export PATH="$GOPATH/bin:$PATH" ;; esac

# less — pager search history
export LESSHISTFILE="${LESSHISTFILE:-$XDG_STATE_HOME/less/history}"

# azure cli
export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$XDG_CONFIG_HOME/azure}"

# aws cli
export AWS_CONFIG_FILE="${AWS_CONFIG_FILE:-$XDG_CONFIG_HOME/aws/config}"
export AWS_SHARED_CREDENTIALS_FILE="${AWS_SHARED_CREDENTIALS_FILE:-$XDG_CONFIG_HOME/aws/credentials}"

# pm2 (node process manager)
export PM2_HOME="${PM2_HOME:-$XDG_DATA_HOME/pm2}"

# ollama — model store
export OLLAMA_MODELS="${OLLAMA_MODELS:-$XDG_DATA_HOME/ollama/models}"
