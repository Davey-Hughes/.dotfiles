#!/usr/bin/env bash
# PreToolUse (Bash) guard: force a confirmation prompt before any recursive rm,
# in every permission mode — including `auto`. A previous unattended session ran
# `rm -rf` with an unset variable that expanded to /* and wiped the machine;
# this makes such deletions always stop for a human "yes".
#
# Emits {permissionDecision:"ask"} when the command looks like a recursive rm.
# Over-asking is safe, under-asking is not, so anything ambiguous asks.

input=$(cat)

ask() {
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Recursive rm detected — confirm before deleting. A prior auto-mode session ran rm -rf with an unset variable expanding to /* and caused data loss."}}'
  exit 0
}

# Prefer jq for an exact read of the command; fall back to scanning the raw
# payload so the guard still fires on a machine without jq.
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
  # jq missing / no command field: coarse scan, err toward asking.
  printf '%s' "$input" | grep -Eq 'rm[[:space:]]+(-[[:alnum:]]*[rR]|--recursive|--no-preserve-root)' && ask
fi
exit 0
