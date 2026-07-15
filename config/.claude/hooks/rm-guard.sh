#!/usr/bin/env bash
# PreToolUse (Bash) guard -- WRAPPER around rm_guard.py.
#
# Runs the real guard and passes its verdict through. If that guard cannot RUN
# at all -- python3 missing, syntax error, file deleted, chmod'd non-executable
# -- this falls back to a crude scan that errs toward asking, instead of
# emitting nothing and letting the command through unexamined.
#
# Why the fallback exists: from 12 Jul to 15 Jul 2026 the guard on this machine
# returned `ask` correctly for every recursive rm, and Claude Code discarded all
# of them because of `skipAutoPermissionPrompt: true` in settings.json. Nothing
# surfaced that. The hook looked healthy in /hooks the entire time. A guard that
# silently does nothing is worse than no guard at all, because you stop looking
# for one. So: never exit quiet on a path that means "I could not check".

input=$(cat)
guard="${BASH_SOURCE[0]%/*}/rm_guard.py"

ask() {
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"rm_guard.py could not run, so this recursive rm was NOT properly checked — falling back to a crude scan. Confirm manually, and look into why the guard is broken."}}'
  exit 0
}

# Exit status is the only signal that separates "ran and had no opinion" from
# "never ran": both produce empty stdout. Trust silence only on exit 0.
if out=$(printf '%s' "$input" | python3 "$guard" 2>/dev/null); then
  case "$out" in
    "")   exit 0 ;;                          # ran, no opinion -> normal flow
    '{'*) printf '%s\n' "$out"; exit 0 ;;    # ran, emitted a verdict
  esac
fi

# --- fallback: guard absent, crashed, or emitted something unparseable --------
# Deliberately crude and over-eager. Known false positives (matches the bare
# word "rm" in prose; treats any -r flag such as `jq -r` as the recursion flag).
# Over-asking is the correct failure here.
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)

if [ -n "$cmd" ]; then
  cmd=${cmd//$'\n'/ }                      # flatten multi-line commands
  has_rm=0 has_rec=0
  # read -ra splits on whitespace WITHOUT glob-expanding, so a literal /* in the
  # command is never expanded by this guard.
  IFS=$' \t' read -ra toks <<< "$cmd"
  for t in "${toks[@]}"; do
    case "${t##*/}" in rm) has_rm=1 ;; esac  # basename match also catches /bin/rm
    case "$t" in
      --recursive|--recursive=*|--no-preserve-root) has_rec=1 ;;
      --*) : ;;                              # other long opts ignored (not --force)
      -*[rR]*) has_rec=1 ;;                  # short bundle with r/R: -rf -fr -Rf -r
    esac
  done
  [ "$has_rm" = 1 ] && [ "$has_rec" = 1 ] && ask
else
  # jq missing / no command field: coarse scan of the raw payload.
  printf '%s' "$input" | grep -Eq 'rm[[:space:]]+(-[[:alnum:]]*[rR]|--recursive|--no-preserve-root)' && ask
fi
exit 0
