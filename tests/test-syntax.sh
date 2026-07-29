#!/usr/bin/env bash
# Parse every config file this repo stows, in the language that will parse it.
#
# A dotfiles repo has no build step, so nothing here is ever compiled before it
# is deployed. The first thing to notice a stray brace in .zshrc is the login
# shell that cannot start -- on the machine you just set up, which is exactly
# when you have the least appetite for it. Parsing is cheap; do it here.
#
# Syntax only, plus shellcheck's warning tier for the scripts that actually run.
# Nothing here executes any of the files it checks: `-n`/`--no-execute` parse and
# stop, which matters because these files rewrite $PATH and $HOME.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

# Tracked files only. An untracked scratch file in the tree is not this repo's
# problem, and CI would never see it anyway.
tracked() { git ls-files | grep -E "$1"; }

check_each() { # check_each <label> <regex over tracked paths> <cmd...>
  local label="$1" pat="$2"; shift 2
  local f out n=0
  local before=$FAILURES
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    n=$((n + 1))
    if ! out=$("$@" "$f" 2>&1); then
      fail "$f"
      [ -n "$out" ] && note "${out%%$'\n'*}"
    fi
  done < <(tracked "$pat")
  if [ "$n" -eq 0 ]; then
    skip "$label -- no tracked files match"
  elif [ "$FAILURES" -eq "$before" ]; then
    ok "$label ($n files)"
  fi
}

# --- POSIX / bash -------------------------------------------------------------
# `.profile` and `xdg-env.sh` are sourced by both bash and zsh, so they are held
# to bash's parser, which is the stricter of the two on the constructs they use.
section "shell scripts"
check_each "bash -n" '(\.sh$|^home/\.(bashrc|bash_profile|profile)$)' bash -n

# --- zsh ----------------------------------------------------------------------
# zsh's own parser, not bash's: .zshrc uses zsh-only syntax (globbing flags,
# `autoload`, ${(f)...} expansions) that bash -n rejects outright.
section "zsh"
if need zsh "zsh startup files"; then
  check_each "zsh -n" '(\.zsh$|\.zsh-theme$|^home/\.zshrc$|^home/\.zshenv$)' zsh -n
fi

# --- fish ---------------------------------------------------------------------
section "fish"
if need fish "fish config and functions"; then
  check_each "fish --no-execute" '\.fish$' fish --no-execute
fi

# --- python -------------------------------------------------------------------
# Covers the rm-guard hook (which a broken parse would silently disable -- see
# the header of config/.claude/hooks/rm-guard.sh) and ~/.pdbrc.py.
section "python"
if need python3 "python hooks and rc files"; then
  # ast.parse and NOT `python3 -m py_compile`: py_compile writes a __pycache__
  # next to each source, i.e. inside a tracked directory, which is the artifact
  # .gitignore already has an entry apologising for. A checker must not litter.
  check_each "ast.parse" '\.py$' python3 -c \
    'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1])'
fi

# --- data formats -------------------------------------------------------------
section "data formats"
if need python3 "json/toml/yaml config"; then
  check_each "json" '\.json$' python3 -c \
    'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))'

  # tomllib is 3.11+. On an older interpreter this skips rather than failing --
  # except in CI, where the container pins a version that has it.
  if python3 -c 'import tomllib' 2>/dev/null; then
    check_each "toml" '\.toml$' python3 -c \
      'import tomllib,sys; tomllib.load(open(sys.argv[1], "rb"))'
  elif [ -n "${CI:-}" ]; then
    fail "python3 has no tomllib, so *.toml went unchecked (CI needs python >= 3.11)"
  else
    skip "toml -- python3 < 3.11, no tomllib"
  fi

  if python3 -c 'import yaml' 2>/dev/null; then
    check_each "yaml" '\.(ya?ml)$' python3 -c \
      'import yaml,sys; yaml.safe_load(open(sys.argv[1], encoding="utf-8"))'
  elif [ -n "${CI:-}" ]; then
    fail "PyYAML is missing, so *.yml went unchecked (CI must install python3-yaml)"
  else
    skip "yaml -- PyYAML not installed"
  fi
fi

# --- shellcheck ---------------------------------------------------------------
# Warning tier and above. `info` and `style` are dropped deliberately: this tree
# carries two unquoted expansions that are load-bearing and annotated as such
# (os/unraid/docker-shell.sh), and a gate that has to be argued with every time
# stops being read.
#
# Applies to scripts that RUN, not to the shell rc files: shellcheck has no zsh
# support at all, and sourced fragments have no shebang for it to dispatch on
# (config/shell/xdg-env.sh carries an explicit `shellcheck shell=` directive).
section "shellcheck (severity >= warning)"
if need shellcheck "shell scripts"; then
  before=$FAILURES
  n=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    n=$((n + 1))
    if ! out=$(shellcheck --severity=warning -f gcc "$f" 2>&1); then
      while IFS= read -r line; do
        [ -n "$line" ] && fail "$line"
      done <<< "$out"
    fi
  done < <(tracked '\.sh$')
  [ "$FAILURES" -eq "$before" ] && ok "shellcheck clean ($n files)"
fi

summary "syntax"
