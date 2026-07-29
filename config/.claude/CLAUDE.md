<!--
Global, user-wide instructions for Claude Code (applies to every project).
Loaded from ~/.config/.claude/CLAUDE.md. Fill in as needed.
-->

## Git

- Do not add attribution to git commits. Never append `Co-Authored-By: Claude` or
  `Generated with Claude Code` (or any similar attribution) to commit messages or PR bodies.

## Shell

- When a shell variable feeds a command that deletes recursively — `rm -r`,
  `find -delete`, `find -exec rm`, `fd -x rm`, `rsync --delete`, `rclone sync`,
  `git clean -f` — write it as `"${VAR:?}"`, never as `"$VAR"`.

  Only the colon form aborts when `VAR` is *set but empty*. `set -u` does not
  catch that case and neither does quoting, so `W=""; rm -rf "$W"/*` expands to
  `rm -rf /*`. The suffix is the whole danger: bare `"$W"` is harmless when
  empty, `"$W"/*` is not.

  This applies to the command you write, not just the one you run — a `${VAR:?}`
  in a script or a README is the version someone copies later.
