#!/usr/bin/env bash
# The three XDG environment files define the same variables.
#
#   config/shell/xdg-env.sh        POSIX, sourced by bash (~/.bashrc, ~/.profile)
#                                  and zsh (~/.zshenv)
#   config/fish/conf.d/xdg.fish    fish
#   config/environment.d/xdg.conf  the systemd user session (Linux GUI + daemons)
#
# All three say "KEEP THESE IN SYNC" in their own headers, and the README claims
# they are. Nothing enforced it, and they had already drifted by two variables
# when this check was written.
#
# Drift here does not fail loudly. It gives one shell a different environment
# than the others: a tool writes its cache to ~/.foo under fish and to
# $XDG_CACHE_HOME/foo under zsh, and the only symptom is the $HOME clutter these
# files exist to prevent, showing up months later with no obvious cause.
#
# NAMES only, not values. Comparing values would mean parsing three different
# expansion syntaxes ("${X:-d}" / `set -q; or set -gx` / systemd's ${X}) and
# would fail on differences that are only spelling.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

SH='config/shell/xdg-env.sh'
FISH='config/fish/conf.d/xdg.fish'
CONF='config/environment.d/xdg.conf'

# --- documented exemptions ----------------------------------------------------
# "VAR file" pairs, one per line, with the reason. A variable listed here is
# allowed to be missing from that one file. Anything else is drift.
exempt() { # exempt <var> <sh|fish|conf> -> reason on stdout, or empty
  case "$1:$2" in
    # environment.d is a static key=value table read once at login: it has no
    # conditionals. Both shell copies set CLAUDE_CONFIG_DIR only when the stowed
    # config dir exists, so that a machine without it falls back to ~/.claude
    # instead of pointing Claude Code at an empty directory. That guard cannot
    # be expressed here, and an unconditional value would break exactly the
    # machines the guard is for.
    CLAUDE_CONFIG_DIR:conf) echo "systemd environment.d cannot express the dir-exists guard" ;;
    *) echo "" ;;
  esac
}

# --- extraction ---------------------------------------------------------------
# The POSIX file has a trailing "Non-XDG shell behaviour" section (currently
# SHELL_SESSIONS_DISABLE) that is deliberately shell-only, so the scan stops
# there. PATH is dropped everywhere: the two shells manage it and the systemd
# file says outright that it does not.
vars_sh() {
  sed '/--- Non-XDG shell behaviour/,$d' "$SH" |
    grep -oE '^[[:space:]]*export [A-Z][A-Z0-9_]*=' |
    sed -E 's/^[[:space:]]*export ([A-Z0-9_]*)=/\1/' | grep -vx PATH | sort -u
}
# NOT anchored to the line start: fish's idiom is `set -q X; or set -gx X ...`,
# so the assignment sits mid-line behind the guard.
vars_fish() {
  grep -oE 'set -gx [A-Z][A-Z0-9_]*' "$FISH" |
    sed -E 's/set -gx //' | grep -vx PATH | sort -u
}
vars_conf() {
  grep -oE '^[A-Z][A-Z0-9_]*=' "$CONF" | tr -d '=' | grep -vx PATH | sort -u
}

section "the three XDG files exist"
for f in "$SH" "$FISH" "$CONF"; do
  [ -f "$f" ] || fail "missing: $f (was it renamed? this check and the README both name it)"
done
[ "$FAILURES" -eq 0 ] || { summary "xdg-sync"; exit 1; }

sh_vars=$(vars_sh); fish_vars=$(vars_fish); conf_vars=$(vars_conf)
ok "$SH: $(printf '%s\n' "$sh_vars" | wc -l | tr -d ' ') vars"
ok "$FISH: $(printf '%s\n' "$fish_vars" | wc -l | tr -d ' ') vars"
ok "$CONF: $(printf '%s\n' "$conf_vars" | wc -l | tr -d ' ') vars"

section "every variable appears in all three (or is documented as exempt)"
before=$FAILURES
exempted=0
all=$(printf '%s\n%s\n%s\n' "$sh_vars" "$fish_vars" "$conf_vars" | sort -u)

has() { printf '%s\n' "$2" | grep -qx "$1"; }

while IFS= read -r var; do
  [ -n "$var" ] || continue
  for pair in "sh:$SH" "fish:$FISH" "conf:$CONF"; do
    key=${pair%%:*}
    file=${pair#*:}
    case "$key" in
      sh)   set_of="$sh_vars" ;;
      fish) set_of="$fish_vars" ;;
      *)    set_of="$conf_vars" ;;
    esac
    has "$var" "$set_of" && continue
    reason=$(exempt "$var" "$key")
    if [ -n "$reason" ]; then
      exempted=$((exempted + 1))
      note "exempt: $var absent from $file -- $reason"
    else
      fail "$var is missing from $file (defined elsewhere, so this is drift)"
    fi
  done
done <<< "$all"

if [ "$FAILURES" -eq "$before" ]; then
  ok "$(printf '%s\n' "$all" | wc -l | tr -d ' ') variables in sync, $exempted documented exemption(s)"
fi

summary "xdg-sync"
