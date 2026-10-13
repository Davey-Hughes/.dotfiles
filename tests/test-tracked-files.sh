#!/usr/bin/env bash
# What this repository is allowed to TRACK.
#
# The only check here whose failure cannot be undone by editing a file.
# config/.claude/ and config/.gemini/ are LIVE tool directories -- the same dirs
# the running Claude Code and Antigravity write sessions, transcripts, OAuth
# state and caches into, on this machine, right now. What keeps that out of a
# published repo is a negation allowlist in .gitignore and nothing else, and a
# commit that leaks one of those files is in the history for good.
#
# Four layers, deliberately overlapping:
#   1. allowlist  under those two dirs, only named entries may be tracked
#   2. denylist   names that must never be tracked ANYWHERE in the tree
#   3. tripwire   the .gitignore patterns themselves still match -- this fires
#                 when the allowlist breaks, which is BEFORE anything is staged,
#                 rather than after the fact like layers 1 and 2
#   4. content    KDE snapshots contain no wallpaper paths, and no obvious
#                 credential shape reached a tracked file
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

# --- 1. allowlist -------------------------------------------------------------
# Mirrors the negation blocks in .gitignore. These two lists drifting apart IS
# the failure mode: .gitignore decides what git ignores, this decides what the
# repo will accept, and only the second one can fail a build.
#
# Space-delimited with leading/trailing spaces so `case` can match whole words.
codex_allow=" AGENTS.md skills themes hooks.json hooks "
codex_hooks_allow=" destructive-guard.py destructive-guard.sh tmux-status.sh tmux-session.py "
codex_themes_allow=" tokyonight-moon.tmTheme "
codex_skills_allow=" prep-pr "
openclaw_allow=" skills.json5 skills tmux-status "
claude_allow=" CLAUDE.md settings.json keybindings.json agents commands hooks output-styles skills themes "
gemini_allow=" settings.json projects.json trustedFolders.json antigravity-cli "
antigravity_allow=" settings.json mcp skills rules hooks plugins sidecars "

check_allowlist() { # check_allowlist <prefix/> <allowlist>
  local prefix="$1" allow="$2" path rest first
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    rest=${path#"$prefix"}
    first=${rest%%/*}
    case "$allow" in
      *" $first "*) ;;
      *) fail "tracked: $path -- '$first' is not in the ${prefix} allowlist" ;;
    esac
  done < <(git ls-files -- "$prefix")
}

section "allowlist: only hand-picked entries under the live tool dirs"
before=$FAILURES
check_allowlist "config/codex/" "$codex_allow"
check_allowlist "config/codex/hooks/" "$codex_hooks_allow"
check_allowlist "config/codex/skills/" "$codex_skills_allow"
check_allowlist "config/codex/themes/" "$codex_themes_allow"
check_allowlist "config/openclaw/" "$openclaw_allow"
check_allowlist "config/.claude/" "$claude_allow"
check_allowlist "config/.gemini/" "$gemini_allow"
check_allowlist "config/.gemini/antigravity-cli/" "$antigravity_allow"
[ "$FAILURES" -eq "$before" ] &&
  ok "$(git ls-files -- config/.claude/ config/.gemini/ | wc -l | tr -d ' ') tracked paths, all allowlisted"

# --- 2. denylist --------------------------------------------------------------
# Names that carry credentials, transcripts or per-machine state whatever
# directory they turn up in. Matched on the basename and on each DIRECTORY
# component -- so config/.gemini/projects.json (a tracked config file) passes
# while config/.claude/projects/ (session transcripts) does not.
deny_basenames='^(\.claude\.json|\.claude\.json\..*|\.credentials\.json|credentials\.json|auth\.json|\.last-cleanup|history\.jsonl|fish_variables|settings\.local\.json|\.env|\.netrc|\.npmrc|id_rsa|id_ecdsa|id_ed25519|.*\.pem)$'
deny_dirs='^(projects|sessions|shell-snapshots|paste-cache|file-history|statsig|todos|security|daemon|jobs|backups|cache|logs|__pycache__)$'

section "denylist: names that must never be tracked anywhere"
before=$FAILURES
while IFS= read -r path; do
  [ -n "$path" ] || continue
  base=${path##*/}
  if [[ $base =~ $deny_basenames ]]; then
    fail "tracked: $path -- '$base' is a denylisted name"
    continue
  fi
  dir=${path%/*}
  [ "$dir" = "$path" ] && continue     # file at the repo root, no components
  while IFS= read -r comp; do
    [ -n "$comp" ] || continue
    [[ $comp =~ $deny_dirs ]] && fail "tracked: $path -- '$comp/' is a denylisted directory"
  done < <(printf '%s\n' "${dir//\// }" | tr ' ' '\n')
done < <(git ls-files)
[ "$FAILURES" -eq "$before" ] && ok "no denylisted name in $(git ls-files | wc -l | tr -d ' ') tracked paths"

# --- 3. tripwire --------------------------------------------------------------
# git check-ignore is pattern-only: it answers for paths that do not exist, so
# this works on a bare CI checkout as well as on the live machine. It fires the
# moment a negation in .gitignore is widened too far -- before anything has been
# staged, which is the only point at which the mistake is still free to fix.
must_ignore=(
  "config/codex/auth.json"
  "config/codex/config.toml"
  "config/codex/config.pre-xdg.toml"
  "config/codex/sessions/session.jsonl"
  "config/codex/plugins/config.json"
  "config/codex/skills/downloaded/SKILL.md"
  "config/openclaw/openclaw.json"
  "config/openclaw/openclaw.json.bak.1"
  "config/openclaw/.env"
  "config/.claude/.claude.json"
  "config/.claude/history.jsonl"
  "config/.claude/projects/-Users-x/session.jsonl"
  "config/.claude/sessions/s.json"
  "config/.claude/security/state.json"
  "config/.claude/shell-snapshots/snapshot.sh"
  "config/.claude/plugins/config.json"
  "config/.claude/statsig/cache"
  "config/.claude/todos/t.json"
  "config/.claude/hooks/__pycache__/rm_guard.cpython-313.pyc"
  "config/.claude/skills/synced/org_account/docx/SKILL.md"
  "config/.gemini/oauth_creds.json"
  "config/.gemini/installation_id"
  "config/.gemini/antigravity-cli/sessions/s.json"
  "config/fish/fish_variables"
  "config/git/config"
  "os/arch/config/ksmserverrc"
  "os/endeavour/config/ksmserverrc"
  "os/endeavour/kde/config/ksmserverrc"
  ".claude/settings.local.json"
)
section "tripwire: .gitignore still covers the sensitive paths"
before=$FAILURES
for p in "${must_ignore[@]}"; do
  # check-ignore also answers "no" for a path that is already TRACKED, so this
  # doubles as an alarm for the case layers 1 and 2 catch after the fact.
  git check-ignore -q "$p" ||
    fail "$p is no longer ignored -- .gitignore was widened, or the path is now tracked"
done
[ "$FAILURES" -eq "$before" ] && ok "${#must_ignore[@]} sensitive paths still ignored"

# --- 4a. KDE snapshots --------------------------------------------------------
# Check the snapshot itself, which also catches copying a raw live config
# before it is committed. Git no longer filters these files.
appletsrc='os/endeavour/kde/config/plasma-org.kde.plasma.desktop-appletsrc'
section "KDE snapshot: no local wallpaper paths"
if [ ! -f "$appletsrc" ]; then
  fail "$appletsrc is missing"
elif grep -qE '^(Image|SlidePaths)=' "$appletsrc"; then
  fail "$appletsrc contains local wallpaper paths -- use kde-config.py capture"
else
  ok "no Image=/SlidePaths= lines in the KDE snapshot"
fi

# --- 4b. credential shapes ----------------------------------------------------
# Narrow on purpose: only token formats that are unambiguous on sight, so this
# never cries wolf over prose. It is a backstop for the path rules above, not a
# general secret scanner.
#
# It excludes ITSELF, because the patterns below are literal token prefixes and
# it would otherwise report its own source on every run. The cost is that this
# one file is unscanned -- so do not paste a real key into it.
section "no credential-shaped strings in tracked files"
before=$FAILURES
while IFS= read -r hit; do
  [ -n "$hit" ] && fail "credential shape: $hit"
done < <(git ls-files -z -- ':(exclude)tests/test-tracked-files.sh' | xargs -0 grep -InE \
  'sk-ant-|ghp_[A-Za-z0-9]{20}|github_pat_|AKIA[0-9A-Z]{16}|xox[baprs]-|-----BEGIN [A-Z ]*PRIVATE KEY-----|ANTHROPIC_API_KEY[[:space:]]*=' \
  2>/dev/null)
[ "$FAILURES" -eq "$before" ] && ok "no API keys, tokens or private keys found"

summary "tracked-files"
