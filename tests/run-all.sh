#!/usr/bin/env bash
# Every check in this repo, in the order CI runs them.
#
#     ./tests/run-all.sh
#
# Same set the Forgejo workflow runs, so a green run here means a green pipeline
# -- with two caveats worth knowing before you trust it:
#
#   * a checker that is not installed SKIPS locally and FAILS in CI, so a laptop
#     without fish or shellcheck reports fewer checks than the pipeline does
#   * test-install.sh runs against a clone of HEAD plus your uncommitted edits,
#     while CI runs against the pushed commit alone
#
# Ordered cheapest-first: the whole suite is under a minute, but the install
# smoke test is most of it, and there is no sense provisioning a fake $HOME to
# discover that a JSON file does not parse.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tests/lib.sh
. "$HERE/lib.sh"
cd_repo_root

FAILED_SUITES=()

run_suite() { # run_suite <label> <cmd...>
  local label="$1"; shift
  printf '\n%s%s %s %s%s\n' "$C_YEL" "========" "$label" "========" "$C_OFF"
  if "$@"; then
    return 0
  fi
  FAILED_SUITES+=("$label")
}

# The rm-guard hook's own suite. It lives beside the hook rather than in here
# because it is stowed with it -- the guard has to remain testable on a machine
# that has ~/.config/.claude but not this repo.
run_suite "rm-guard"       python3 config/.claude/hooks/test_rm_guard.py
run_suite "tracked-files"  "$HERE/test-tracked-files.sh"
run_suite "docs"           "$HERE/test-docs.sh"
run_suite "xdg-sync"       "$HERE/test-xdg-sync.sh"
run_suite "syntax"         "$HERE/test-syntax.sh"
run_suite "install"        "$HERE/test-install.sh"

printf '\n%s\n' "----------------------------------------"
if [ ${#FAILED_SUITES[@]} -eq 0 ]; then
  printf '%sall suites passed%s\n' "$C_GRN" "$C_OFF"
  exit 0
fi
printf '%sfailed: %s%s\n' "$C_RED" "${FAILED_SUITES[*]}" "$C_OFF" >&2
exit 1
