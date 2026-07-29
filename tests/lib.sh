# Shared helpers for the checks under tests/. Sourced, never executed.
#
# Every check reports EVERY failure it finds before exiting, rather than dying on
# the first one. A dotfiles tree breaks in clusters -- one rename leaves six dead
# references behind it -- and discovering them one CI round-trip at a time is the
# slow way to do it.
#
# shellcheck shell=bash

# Colour when a terminal is attached, and under Forgejo Actions (which renders
# ANSI in the job log but is not a tty). Never when redirected to a file.
if [ -t 1 ] || [ -n "${CI:-}" ]; then
  C_RED=$'\033[1;31m'; C_GRN=$'\033[1;32m'; C_YEL=$'\033[1;33m'; C_OFF=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_OFF=''
fi

FAILURES=0

section() { printf '\n%s==>%s %s\n' "$C_YEL" "$C_OFF" "$1"; }
ok()      { printf '   %sok%s   %s\n' "$C_GRN" "$C_OFF" "$1"; }
skip()    { printf '   %sskip%s %s\n' "$C_YEL" "$C_OFF" "$1"; }
note()    { printf '        %s\n' "$1"; }
fail()    { printf '   %sFAIL%s %s\n' "$C_RED" "$C_OFF" "$1" >&2
            FAILURES=$((FAILURES + 1)); }

# FAILURES is a plain variable, so a `cmd | while read` loop would count in a
# SUBSHELL and throw the increments away -- a check that finds problems and
# still exits 0. Every loop that can call fail() must therefore be fed by
# `< <(cmd)` process substitution, not by a pipe. Same for the whole script:
# never `./check.sh | tee`, which would put fail() one level down again.

# cd to the repo root so a check behaves identically from any directory, and so
# `git ls-files` yields paths relative to it.
cd_repo_root() {
  local root
  root=$(git rev-parse --show-toplevel) || {
    printf 'not inside a git repository\n' >&2
    exit 2
  }
  cd "$root" || exit 2
}

# A tool that is merely absent on a laptop is a hard failure in CI: the whole
# point of the pipeline is that nothing is silently unchecked there.
need() { # need <command> <what it would check>
  command -v "$1" >/dev/null 2>&1 && return 0
  if [ -n "${CI:-}" ]; then
    fail "$1 is not installed, so $2 went unchecked (CI must have it)"
  else
    skip "$2 -- $1 not installed"
  fi
  return 1
}

summary() {
  if [ "$FAILURES" -eq 0 ]; then
    printf '\n%s%s passed%s\n' "$C_GRN" "$1" "$C_OFF"
    return 0
  fi
  printf '\n%s%s: %d failure(s)%s\n' "$C_RED" "$1" "$FAILURES" "$C_OFF" >&2
  return 1
}
