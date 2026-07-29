#!/usr/bin/env bash
# Every repo path the documentation names actually exists.
#
# The docs here are not decoration: the README is the instructions for setting
# up a machine, and a path in it that no longer resolves sends you looking for a
# file that was renamed two commits ago. This repo already shipped a README
# pointing at a `docs/xdg-home-audit.md` that was never committed at all.
#
# Two kinds of reference are checked:
#   [text](path)   markdown links, resolved against the document's own directory
#   `config/x/y`   backticked paths, resolved against the repo root -- these are
#                  how this README actually cites files, so link-checking alone
#                  would have missed the dead one
#
# URLs are not fetched. A network check would make the suite fail for reasons
# that have nothing to do with the commit under test.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

# A backticked token is treated as a repo path when it has a separator and
# nothing that marks it as a placeholder or an expansion.
#
# Note what is NOT required: that its first component already exists. Requiring
# that would skip `docs/xdg-home-audit.md` -- a reference to a directory this
# repo has never had -- which is precisely the failure this check is for.
is_repo_path() {
  case "$1" in
    */*) ;;
    *) return 1 ;;
  esac
  case "$1" in
    # Placeholders and shell expansions: `os/<platform>/config`, `~/.config/`,
    # `$XDG_CONFIG_HOME/git`, globs. Nothing to resolve, so nothing to check.
    *[\<\>\*\~\$\ ]*) return 1 ;;
    # Absolute paths name the machine, not the repo; URLs are not fetched.
    /*|http://*|https://*) return 1 ;;
  esac
  return 0
}

section "documentation references resolve"
before=$FAILURES
checked=0

while IFS= read -r doc; do
  [ -n "$doc" ] || continue
  docdir=${doc%/*}
  [ "$docdir" = "$doc" ] && docdir="."

  # --- backticked repo paths --------------------------------------------------
  # Root-level markdown only. A document nested inside config/ is tool content
  # rather than documentation of this repo -- the ADHD skill, for one, is
  # generic writing guidance whose `src/auth.ts` examples are invented on
  # purpose and would fail every run.
  if [ "$docdir" = "." ]; then
    while IFS= read -r tok; do
      [ -n "$tok" ] || continue
      is_repo_path "$tok" || continue
      checked=$((checked + 1))
      [ -e "$tok" ] || fail "$doc names \`$tok\`, which does not exist"
    done < <(grep -oE '`[^`]+`' "$doc" | tr -d '`' | sort -u)
  fi

  # --- markdown relative links ------------------------------------------------
  while IFS= read -r link; do
    [ -n "$link" ] || continue
    case "$link" in
      http://*|https://*|mailto:*|\#*) continue ;;
    esac
    link=${link%%#*}                        # drop any #anchor
    [ -n "$link" ] || continue
    checked=$((checked + 1))
    [ -e "$docdir/$link" ] || fail "$doc links to $link, which does not exist"
  done < <(grep -oE '\]\([^)]+\)' "$doc" | sed -E 's/^\]\(//; s/\)$//' | sort -u)
done < <(git ls-files '*.md')

if [ "$FAILURES" -eq "$before" ]; then
  ok "$checked reference(s) across $(git ls-files '*.md' | wc -l | tr -d ' ') markdown file(s)"
fi

summary "docs"
