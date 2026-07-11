#!/usr/bin/env bash
#
# Relocate existing tool data from $HOME into the XDG locations defined in
# config/shell/xdg-env.sh. Run this once on a machine after installing the
# dotfiles to move pre-existing dirs; the shell env only redirects *future*
# writes, it does not move what is already there.
#
# Idempotent and non-destructive: skips anything already migrated (target
# exists) or absent. Dry-run by default — pass --apply to actually move.
#
# Docker (Docker Desktop hardcodes ~/.docker) and nuget (single contained dir)
# are intentionally NOT migrated; see docs/xdg-home-audit.md.

set -euo pipefail

APPLY=0
case "${1:-}" in
  --apply) APPLY=1 ;;
  ""|--dry-run) APPLY=0 ;;
  *) echo "usage: $0 [--apply]" >&2; exit 2 ;;
esac

: "${XDG_CONFIG_HOME:=$HOME/.config}"
ENV_FILE="$XDG_CONFIG_HOME/shell/xdg-env.sh"
if [ ! -r "$ENV_FILE" ]; then
  echo "error: $ENV_FILE not found — run install.sh first." >&2
  exit 1
fi
# Pull in CARGO_HOME, RUSTUP_HOME, GOPATH, NPM_CONFIG_CACHE, ... from one source.
# shellcheck disable=SC1090
. "$ENV_FILE"

to_move=0 skipped=0
move() { # label  src  dest
  local label="$1" src="$2" dest="$3"
  [ -e "$src" ] || return 0
  if [ -e "$dest" ]; then
    printf '  skip   %-14s %s (target exists)\n' "$label" "$src"
    skipped=$((skipped + 1))
    return 0
  fi
  to_move=$((to_move + 1))
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$(dirname "$dest")"
    mv "$src" "$dest"
    printf '  moved  %-14s %s -> %s\n' "$label" "$src" "$dest"
  else
    printf '  would move %-11s %s -> %s\n' "$label" "$src" "$dest"
  fi
}

if [ "$APPLY" -eq 1 ]; then echo "XDG home migration (APPLY):"; else echo "XDG home migration (dry-run):"; fi

move cargo     "$HOME/.cargo"             "$CARGO_HOME"
move rustup    "$HOME/.rustup"            "$RUSTUP_HOME"
move go        "$HOME/go"                 "$GOPATH"
move npm       "$HOME/.npm"               "$NPM_CONFIG_CACHE"
move dotnet    "$HOME/.dotnet"            "$DOTNET_CLI_HOME"
move ipython   "$HOME/.ipython"           "$IPYTHONDIR"
move jupyter   "$HOME/.jupyter"           "$XDG_CONFIG_HOME/jupyter"
move node-hist "$HOME/.node_repl_history" "$NODE_REPL_HISTORY"
move py-hist   "$HOME/.python_history"    "$PYTHON_HISTORY"
move redis-hist "$HOME/.rediscli_history" "$REDISCLI_HISTFILE"
move psql-hist "$HOME/.psql_history"      "$PSQL_HISTORY"
move wget-hsts "$HOME/.wget-hsts"         "$XDG_STATE_HOME/wget/wget-hsts"
move gitconfig  "$HOME/.gitconfig"        "$XDG_CONFIG_HOME/git/config"
move fontconfig "$HOME/.fonts.conf"       "$XDG_CONFIG_HOME/fontconfig/fonts.conf"
move bash-hist  "$HOME/.bash_history"     "$XDG_STATE_HOME/bash/history"
move zsh-hist   "$HOME/.histfile"         "$XDG_STATE_HOME/zsh/history"
move azure      "$HOME/.azure"            "$AZURE_CONFIG_DIR"
move aws        "$HOME/.aws"              "$XDG_CONFIG_HOME/aws"
move pm2        "$HOME/.pm2"              "$PM2_HOME"
move ts-node-hist "$HOME/.ts_node_repl_history" "$TS_NODE_HISTORY"
move bun        "$HOME/.bun"              "$BUN_INSTALL"
move gem-specs  "$HOME/.gem/specs"        "$GEM_SPEC_CACHE"

echo ""
printf '%d to move, %d already migrated.\n' "$to_move" "$skipped"
if [ "$APPLY" -eq 0 ] && [ "$to_move" -gt 0 ]; then
  echo "Re-run with --apply to perform the moves."
fi
