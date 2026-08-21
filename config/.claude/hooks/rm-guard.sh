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

# Plain text, no SGR. This used to open bold red, matching rm_guard.py, until
# Claude Code 2.1.235 started sanitising hook-supplied strings: every code point
# below 32 is replaced with U+FFFD, so an ESC arrived as a replacement glyph and
# the line opened `<?>[1;31mrm_guard.py could not run`. Leading with the failure
# is what carries it now -- see the note above MAX_ECHO in rm_guard.py.
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
#
# Two things it does NOT ask about, matching rm_guard.py's scope: rm without a
# recursion flag, and `git rm` (a working-tree operation, recoverable from
# history). Both are tracked per pipeline segment so `git rm -r x && rm -rf /`
# still asks on the second segment.
#
# Note this path only ever ASKS. It has no allow, so it cannot do what
# rm_guard.py's RISKY_NEIGHBOURS exists to stop -- hand a whole tool call a
# blanket approval on the strength of one operand it happened to clear.
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)

# Each rule is a (command seen, danger flag seen) pair within one segment. A
# lone `find` is fine and a lone `-delete` is fine; together they are not.
verdict() {
  [ "$n_rm"     = 1 ] && [ "$f_rec"    = 1 ] && [ "$is_git" = 0 ] && ask
  [ "$n_find"   = 1 ] && [ "$f_finddel" = 1 ] && ask
  [ "$n_fd"     = 1 ] && [ "$f_fdexec"  = 1 ] && ask
  [ "$n_rsync"  = 1 ] && [ "$f_rsdel"   = 1 ] && ask
  [ "$n_rclone" = 1 ] && [ "$f_rcact"   = 1 ] && ask
  [ "$is_git"   = 1 ] && [ "$f_clean"   = 1 ] && ask   # git clean, unlike git rm
  [ "$is_git"   = 1 ] && [ "$f_wt"      = 1 ] && [ "$f_rmv" = 1 ] && ask
  return 0
}
reset_seg() { n_rm=0 n_find=0 n_fd=0 n_rsync=0 n_rclone=0
              f_rec=0 f_finddel=0 f_fdexec=0 f_rsdel=0 f_rcact=0 f_clean=0
              f_wt=0 f_rmv=0 is_git=0 head=1; }

if [ -n "$cmd" ]; then
  cmd=${cmd//$'\n'/ }                      # flatten multi-line commands
  # read -ra splits on whitespace WITHOUT glob-expanding, so a literal /* in the
  # command is never expanded by this guard.
  IFS=$' \t' read -ra toks <<< "$cmd"
  reset_seg
  for t in "${toks[@]}"; do
    t=${t//\"/}; t=${t//\'/}                 # crude unquoting, so `ssh h "find ..."`
    [ -n "$t" ] || continue                  # still exposes its inner words
    case "$t" in
      ';'|'&&'|'||'|'|'|'&') verdict; reset_seg; continue ;;
    esac
    if [ "$head" = 1 ]; then                 # command word, modulo wrappers
      case "${t##*/}" in
        sudo|command|env|nohup|time|nice) ;;
        *=*) ;;                              # leading VAR=value assignment
        git) is_git=1; head=0 ;;
        *) head=0 ;;
      esac
    fi
    case "${t##*/}" in                       # basename also catches /bin/rm
      rm)         n_rm=1 ;;
      find)       n_find=1 ;;
      fd|fdfind)  n_fd=1 ;;
      rsync)      n_rsync=1 ;;
      rclone)     n_rclone=1 ;;
    esac
    case "$t" in
      -delete|-exec|-execdir|-ok|-okdir)            f_finddel=1 ;;
      -x|-X|--exec|--exec-batch)                    f_fdexec=1 ;;
      --delete|--delete-*|--del|--remove-source-files) f_rsdel=1 ;;
      worktree)                                     f_wt=1 ;;
      remove)                                       f_rmv=1 ;;
      sync|purge|delete|deletefile|rmdir|rmdirs|move|cleanup) f_rcact=1 ;;
      clean)                                        f_clean=1 ;;
      --recursive|--recursive=*|--no-preserve-root) f_rec=1 ;;
      --*) : ;;                              # other long opts ignored (not --force)
      -*[rR]*) f_rec=1 ;;                    # short bundle with r/R: -rf -fr -Rf -r
    esac
  done
  verdict
else
  # jq missing / no command field: coarse scan of the raw payload. `git rm` is
  # rewritten to `git-rm` first; the left-hand boundary class excludes `-`, so
  # the rewritten form no longer matches.
  printf '%s' "$input" \
    | sed -E 's/(^|[^A-Za-z0-9_-])git[[:space:]]+rm([^A-Za-z0-9_-]|$)/\1git-rm\2/g' \
    | grep -Eq '(^|[^A-Za-z0-9_-])rm[[:space:]]+(-[[:alnum:]]*[rR]|--recursive|--no-preserve-root)|(^|[^A-Za-z0-9_-])(-delete|-execdir|-exec|--delete|--del|--exec-batch|--remove-source-files)([^A-Za-z0-9_-]|$)|rclone[[:space:]]+(sync|purge|delete|move|cleanup)|clean[[:space:]]+-[[:alnum:]]*f|worktree[[:space:]]+remove' && ask
fi
exit 0
