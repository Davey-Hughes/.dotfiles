# Personal instructions

## Communication

- Lead with the answer or concrete next action. Keep tangents out.
- Number multi-step instructions; keep lists short and actions bounded.
- During ongoing work, make progress and the next step visible without making
  the reader remember earlier turns. End with one next action when work remains.
- State errors matter-of-factly: cause, consequence, fix. Avoid canned preambles,
  closing pleasantries, and unsupported time estimates.

## Attribution

- Do not add AI or assistant credit lines, badges, banners, markers, or
  coauthor trailers to commits, PRs, comments, documentation, or other output.
  Preserve existing authorship, licenses, and repository-required sign-offs.

## Shell

- When a shell variable feeds a recursive deletion or destructive sync, use
  `"${VAR:?}"`, never plain `"$VAR"`. This includes `rm -r`, `find -delete`,
  `find -exec rm`, `fd -x rm`, `rsync --delete`, `rclone sync`, and `git clean -f`.
  Apply this to scripts and examples as well as commands you execute.
  Quoting and `set -u` do not protect against a variable set to an empty string.

## Files and home directory

- Keep `$HOME` sparse. Do not create new top-level files or directories there
  unless explicitly requested or required by an existing tool configuration.
- Put deliverables in the project. Keep scratch scripts, logs, plans, and status
  reports out of the working tree unless requested.
- Use a provided session scratch directory for temporary work, otherwise a
  unique directory under `${TMPDIR:-/tmp}`. Do not use the home directory.
- Put durable tool configuration, data, state, and caches in the corresponding
  XDG directories. Prefer a tool's redirect flag or environment variable when
  it would otherwise create new home entries. If it cannot be redirected,
  explain that before running it. Preserve existing tool state when relocating.
