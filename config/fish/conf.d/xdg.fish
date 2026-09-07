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

# less — pager search history
set -q LESSHISTFILE; or set -gx LESSHISTFILE $XDG_STATE_HOME/less/history

# azure cli
set -q AZURE_CONFIG_DIR; or set -gx AZURE_CONFIG_DIR $XDG_CONFIG_HOME/azure

# aws cli
set -q AWS_CONFIG_FILE; or set -gx AWS_CONFIG_FILE $XDG_CONFIG_HOME/aws/config
set -q AWS_SHARED_CREDENTIALS_FILE; or set -gx AWS_SHARED_CREDENTIALS_FILE $XDG_CONFIG_HOME/aws/credentials

# pm2 (node process manager)
set -q PM2_HOME; or set -gx PM2_HOME $XDG_DATA_HOME/pm2

# ts-node — REPL history
set -q TS_NODE_HISTORY; or set -gx TS_NODE_HISTORY $XDG_STATE_HOME/ts-node/history

# bun — home + install cache. PATH ($BUN_INSTALL/bin) is added by fish_add_path in config.fish.
set -q BUN_INSTALL; or set -gx BUN_INSTALL $XDG_DATA_HOME/bun
set -q BUN_INSTALL_CACHE_DIR; or set -gx BUN_INSTALL_CACHE_DIR $XDG_CACHE_HOME/bun

# rubygems — relocate the API source-index spec cache (~/.gem/specs)
set -q GEM_SPEC_CACHE; or set -gx GEM_SPEC_CACHE $XDG_CACHE_HOME/gem

# claude code — config dir (plugins, sessions, memory) under XDG instead of ~/.claude.
# Lives here (not the is-interactive block in config.fish) so NON-interactive fish — GUI
# launches, task runners, other shells' subprocesses — resolve the same store. Mirrors the
# POSIX copy in shell/xdg-env.sh. Guarded on the dir existing so a machine without the stowed
# config falls back to ~/.claude rather than pointing at an empty dir.
if test -d $XDG_CONFIG_HOME/.claude
    set -q CLAUDE_CONFIG_DIR; or set -gx CLAUDE_CONFIG_DIR $XDG_CONFIG_HOME/.claude
end

# antigravity (gemini cli) — config dir under XDG. Unguarded, unlike Claude
# above: the CLI creates the directory itself when it is missing, so pointing at
# a not-yet-existing path costs nothing.
set -q ANTIGRAVITY_CONFIG_DIR; or set -gx ANTIGRAVITY_CONFIG_DIR $XDG_CONFIG_HOME/.gemini/antigravity-cli

# android — the SDK, plus the tools' user dir (AVDs, adb keys, caches) that
# would otherwise sit in ~/Android/Sdk and ~/.android.
#
# Set unconditionally, unlike everything above. Arch's
# android-sdk-cmdline-tools-latest ships a /etc/profile.d snippet exporting
# ANDROID_HOME=/opt/android-sdk into the session, so a `set -q` guard would
# keep that value — and /opt holds only cmdline-tools, none of the platforms,
# NDKs or system images.
#
# ANDROID_USER_HOME is the modern name and the newer tools honour it, but the
# emulator (37.1.11) still only searches $ANDROID_AVD_HOME, $ANDROID_SDK_HOME
# and $HOME/.android — hence the two extra variables rather than the one.
set -gx ANDROID_HOME $XDG_DATA_HOME/android-sdk
set -gx ANDROID_SDK_ROOT $ANDROID_HOME
set -gx ANDROID_USER_HOME $XDG_DATA_HOME/android
set -gx ANDROID_EMULATOR_HOME $ANDROID_USER_HOME
set -gx ANDROID_AVD_HOME $ANDROID_USER_HOME/avd
