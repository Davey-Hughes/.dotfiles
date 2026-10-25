#!/usr/bin/env bash
#
# tmux-resurrect save hook: send every OpenCode pane back to the session it was
# showing on restore.
#
# resurrect records an OpenCode pane as the command that started it, usually a
# bare `opencode`, which opens the home screen. The TUI can switch sessions at
# any time, so only the TUI knows which one a pane shows: the CLI plugin in
# ~/.config/opencode/plugins/tmux-status keeps it in the pane option
# @oc_session, and drops it on the home screen and on exit. This rewrites each
# such pane's command in the save file to `opencode --session <id>`.
# ~/.tmux.conf lists "opencode --session" in @resurrect-processes, which
# matches only the rewritten form, so a pane this could not place comes back
# as a shell.
#
# The session id ends up typed into a shell, so nothing but an OpenCode session
# id gets that far.
#
# Never exits non-zero, and touches the save file only by renaming a complete
# replacement over it.
#
# Usage:
#   resurrect-opencode.sh <save file>    @resurrect-hook-post-save-layout

set -uo pipefail

file="${1:-}"
[[ -f "$file" ]] || exit 0
command -v tmux >/dev/null 2>&1 || exit 0

readonly SESSION_ID='^ses_[A-Za-z0-9]+$'

map=''
while IFS=$'\t' read -r command session window index id; do
    [[ "$command" == opencode && "$id" =~ $SESSION_ID ]] || continue
    map+="$session"$'\t'"$window"$'\t'"$index"$'\t'"$id"$'\n'
done < <(tmux list-panes -a -F \
    $'#{pane_current_command}\t#{session_name}\t#{window_index}\t#{pane_index}\t#{@oc_session}' 2>/dev/null)
[[ -n "$map" ]] || exit 0

# resurrect's pane line: $2 session, $3 window, $6 pane, $10 the command tmux
# shows for the pane, $11 ":" and the command to restart.
tmp="$file.opencode.$$"
MAP="$map" awk '
    BEGIN {
        FS = OFS = "\t"
        n = split(ENVIRON["MAP"], row, "\n")
        for (i = 1; i < n; i++) {
            split(row[i], f, FS)
            id[f[1] FS f[2] FS f[3]] = f[4]
        }
    }
    $1 == "pane" && NF == 11 && $10 == "opencode" && ($2 FS $3 FS $6) in id {
        $11 = ":opencode --session " id[$2 FS $3 FS $6]
    }
    { print }
' "$file" > "$tmp" &&
    [[ "$(wc -l < "$tmp")" -eq "$(wc -l < "$file")" ]] &&
    mv -f "$tmp" "$file"
rm -f "$tmp"
exit 0
