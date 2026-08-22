#!/usr/bin/env bash
# install.sh, end to end, against a throwaway $HOME.
#
# This is the only script in the repo that has never run anywhere except an
# already-converged machine, where nearly all of it is a no-op. Everything worth
# testing only happens on a FRESH home directory: stow's directory folding, the
# conflict/backup path, and whether a second run is actually idempotent.
#
# ISOLATION. install.sh derives every path it touches from $HOME
# (DOTFDIR=$HOME/.dotfiles, BACKUP_DIR, XDG_CONFIG_HOME), so a temp $HOME
# contains it -- with three exceptions, handled below:
#
#   brew bundle   would install the entire Brewfile system-wide.  stubbed
#   tmux          would start a server and drive tpm against it.  stubbed
#   git clone ~/.vim  network fetch of a separate repo.           pre-created
#
# The stubs go on PATH rather than being patched into install.sh: the script
# under test stays exactly the script that ships.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root
ROOT=$(pwd)

need stow "install.sh (it exits early without stow)" || { summary "install"; exit "$FAILURES"; }

FAKE=$(mktemp -d "${TMPDIR:-/tmp}/dotfiles-install-test.XXXXXX") || exit 2
# "${FAKE:?}" and not "$FAKE": if mktemp ever failed leaving FAKE empty, the
# colon form aborts, and the plain form would expand to `rm -rf /*`. set -u does
# not catch set-but-empty, and quoting does not either.
cleanup() { rm -rf "${FAKE:?}"; }
trap cleanup EXIT

section "fixture: a fresh \$HOME at $FAKE"

# The clone is of HEAD, so CI tests exactly the commit that triggered it.
git clone --quiet --no-hardlinks "$ROOT" "$FAKE/.dotfiles" || {
  fail "could not clone the repo into the fixture"; summary "install"; exit 1; }

# Locally, overlay uncommitted edits so this tests what you are about to commit
# rather than what you last committed. Never fires in CI, where the tree is clean.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  while IFS= read -r f; do
    [ -f "$ROOT/$f" ] || continue          # skip deletions
    case "$f" in */*) mkdir -p "$FAKE/.dotfiles/${f%/*}" ;; esac
    cp "$ROOT/$f" "$FAKE/.dotfiles/$f"
  done < <(git ls-files --modified)
  note "working tree is dirty -- uncommitted edits overlaid onto the fixture"
fi

# Baseline AFTER the overlay: those edits are the fixture's starting state, so
# only what install.sh itself changes should show up as a difference later.
fixture_status=$(git -C "$FAKE/.dotfiles" status --porcelain)

# ~/.vim is a separate repo that install.sh clones from GitHub when absent.
# Pre-creating it takes that network fetch out of the test; the ~/.config/nvim
# link it feeds is still asserted below.
mkdir -p "$FAKE/.vim"

mkdir -p "$FAKE/bin"
printf '#!/bin/sh\nexit 0\n' > "$FAKE/bin/brew"
printf '#!/bin/sh\nexit 0\n' > "$FAKE/bin/tmux"
chmod +x "$FAKE/bin/brew" "$FAKE/bin/tmux"

# A pre-existing real file where a symlink wants to go, so the conflict/backup
# path runs instead of being skipped. This is the branch that decides whether a
# fresh machine keeps or loses its existing config.
printf '# pre-existing zshrc, must be preserved\n' > "$FAKE/.zshrc"

run_install() {
  # XDG_CONFIG_HOME unset on purpose: the default ($HOME/.config) is the path a
  # fresh machine takes. GIT_CONFIG_GLOBAL unset so git_settings' own export is
  # what takes effect.
  env -u XDG_CONFIG_HOME -u GIT_CONFIG_GLOBAL \
      HOME="$FAKE" PATH="$FAKE/bin:$PATH" \
      bash "$FAKE/.dotfiles/install.sh" > "$FAKE/run$1.log" 2>&1
}

# --- assertions ---------------------------------------------------------------
# Follows the leaf's symlink chain, then resolves the containing directory
# physically. Both halves are needed: stow FOLDS a package directory into a
# single link when only one package provides it, so ~/.config/fish is itself the
# symlink and ~/.config/fish/config.fish is a plain file reached through it.
# Asserting "the leaf is a symlink" would call that correct layout a failure.
# (`readlink -f` would do this in one call, but it is GNU-only.)
realpath_of() {
  local p="$1" target
  while [ -L "$p" ]; do
    target=$(readlink "$p")
    case "$target" in
      /*) p="$target" ;;
      *)  p="$(dirname "$p")/$target" ;;
    esac
  done
  if [ -d "$p" ]; then
    (cd "$p" && pwd -P)
  else
    printf '%s/%s\n' "$(cd "$(dirname "$p")" && pwd -P)" "$(basename "$p")"
  fi
}

resolves_into_repo() { # <path> <expected repo-relative target>
  [ -e "$1" ] || { fail "$1 does not exist"; return 1; }
  local resolved
  resolved=$(realpath_of "$1")
  case "$resolved" in
    */.dotfiles/"$2") ok "${1#"$FAKE/"} -> $2" ;;
    *) fail "${1#"$FAKE/"} resolves to $resolved, expected .../.dotfiles/$2" ;;
  esac
}

section "first run: fresh machine"
if run_install 1; then
  ok "install.sh exited 0"
else
  fail "install.sh exited non-zero -- log below"
  while IFS= read -r line; do note "$line"; done < <(tail -20 "$FAKE/run1.log")
fi

resolves_into_repo "$FAKE/.zshrc"                  "home/.zshrc"
resolves_into_repo "$FAKE/.tmux.conf"              "home/.tmux.conf"
resolves_into_repo "$FAKE/.config/starship.toml"   "config/starship.toml"
resolves_into_repo "$FAKE/.config/fish/config.fish" "config/fish/config.fish"
resolves_into_repo "$FAKE/.config/git/ignore"      "config/git/ignore"

section "the conflicting file was preserved, not clobbered"
backup=$(find "$FAKE/.dotfiles-backup" -name '.zshrc' -type f 2>/dev/null | head -1)
if [ -n "$backup" ] && grep -q 'pre-existing zshrc' "$backup"; then
  ok "the old ~/.zshrc is intact at ${backup#"$FAKE/"}"
else
  fail "the pre-existing ~/.zshrc was not backed up (looked under ~/.dotfiles-backup)"
fi

section "stow folded nothing that other software writes into"
# Stow turns a package directory into ONE symlink when the target does not exist
# yet. Wherever something other than this repo also writes there, every such
# write then lands inside the checkout: git_settings' global config, fish's
# fish_variables, Claude Code's credentials and session state, TPM's plugins,
# and the .desktop files pacman/Steam/Lutris drop in.
#
# Spelled out here rather than read from install.sh ON PURPOSE, the same way the
# allowlist in test-tracked-files.sh mirrors .gitignore: sourcing UNFOLD_HOME
# would make deleting an entry delete its own assertion, and the regression this
# exists to catch IS someone dropping an entry. install.sh declares the
# behaviour, this decides what the repo will accept, and only this can fail a
# build. Adding a directory means adding it in both places.
expect_home=(.local/bin .local/share/applications .tmux/plugins)
expect_config=(.claude fish git systemd/user)
before=$FAILURES
unfold_targets=()
for d in "${expect_home[@]}";   do unfold_targets+=("$FAKE/$d"); done
for d in "${expect_config[@]}"; do unfold_targets+=("$FAKE/.config/$d"); done
for t in "${unfold_targets[@]}"; do
  rel=${t#"$FAKE/"}
  # The `~/` is display text, not a path being used: these checks run against
  # $FAKE, so $HOME would print the real home and name a file nothing looked at.
  # shellcheck disable=SC2088
  if [ -L "$t" ]; then
    fail "~/$rel is a symlink -- writes to it would land inside the repo"
  elif [ ! -d "$t" ]; then
    fail "~/$rel does not exist"
  fi
done
[ "$FAILURES" -eq "$before" ] &&
  ok "${#unfold_targets[@]} shared directories are real, not folded"

# The flip side: a submodule must still reach the repo's checkout as one whole
# directory, or it stops being a usable git repo and tpm can no longer update
# itself. It does not need a link of its own -- a folded ancestor (~/.zsh) gets
# it there just as well -- so this checks where the path RESOLVES, not its type.
# Unfolded into per-file links, it would resolve to a real directory under $HOME.
section "submodules still resolve into the repo"
before=$FAILURES
while IFS= read -r sub; do
  case "$sub" in home/*) t="$FAKE/${sub#home/}" ;; config/*) t="$FAKE/.config/${sub#config/}" ;; *) continue ;; esac
  [ -e "$t" ] || continue
  if [ "$(readlink -f "$t")" != "$(readlink -f "$FAKE/.dotfiles/$sub")" ]; then
    fail "${t#"$FAKE/"} does not resolve into the checkout -- the submodule was unfolded into per-file links"
  fi
done < <(git config -f .gitmodules --get-regexp '^submodule\..*\.path$' | awk '{print $2}')
[ "$FAILURES" -eq "$before" ] && ok "each submodule resolves to its directory in the checkout"
if [ -f "$FAKE/.config/git/config" ] && ! [ -L "$FAKE/.config/git/config" ]; then
  grep -q 'defaultBranch' "$FAKE/.config/git/config" &&
    ok "the global git config was written there, outside the repo" ||
    fail "the ~/.config/git/config file exists but git_settings' keys are missing"
else
  fail "git_settings did not write ~/.config/git/config"
fi
# Compared against the post-overlay baseline, so a locally dirty tree does not
# read as install.sh having written into its own checkout.
if [ "$(git -C "$FAKE/.dotfiles" status --porcelain)" = "$fixture_status" ]; then
  ok "install.sh wrote nothing into its own checkout"
else
  fail "install.sh dirtied its own checkout:"
  while IFS= read -r line; do note "$line"; done < <(
    diff <(printf '%s\n' "$fixture_status") \
         <(git -C "$FAKE/.dotfiles" status --porcelain) | head -10)
fi

section "the ~/.vim cross-link"
if [ -L "$FAKE/.config/nvim" ] && [ -d "$FAKE/.config/nvim" ]; then
  ok "the ~/.config/nvim link resolves to ~/.vim"
else
  fail "the ~/.config/nvim link is not a working link to ~/.vim"
fi

section "no dangling symlinks were created"
# -L makes find follow links, so -type l matches only those whose target is
# missing. The submodule dirs are the usual suspects when this fires.
before=$FAILURES
while IFS= read -r dead; do
  [ -n "$dead" ] && fail "dangling: ${dead#"$FAKE/"} -> $(readlink "$dead")"
done < <(find -L "$FAKE" -maxdepth 4 -type l -not -path "$FAKE/.dotfiles/*" 2>/dev/null)
[ "$FAILURES" -eq "$before" ] && ok "every symlink under the fake \$HOME resolves"

section "second run: idempotent"
backups_before=$(find "$FAKE/.dotfiles-backup" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
if run_install 2; then
  ok "install.sh exited 0 again"
else
  fail "the second run exited non-zero -- log below"
  while IFS= read -r line; do note "$line"; done < <(tail -20 "$FAKE/run2.log")
fi
backups_after=$(find "$FAKE/.dotfiles-backup" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
if [ "$backups_before" = "$backups_after" ]; then
  ok "no new backup directory (nothing was moved aside twice)"
else
  fail "the second run created another backup dir ($backups_before -> $backups_after): it treated its own symlinks as conflicts"
fi
resolves_into_repo "$FAKE/.zshrc" "home/.zshrc"

summary "install"
