#!/usr/bin/env bash
# tmux-session.sh -- which OpenClaw session each tmux pane is showing.
#
# Claude Code and Codex run their hooks inside the pane, so the hooks inherit
# $TMUX_PANE. OpenClaw's agent loop runs in the gateway daemon. Its hooks only
# know the session key, and the TUI client in the pane can switch sessions at
# any time (/session, /new, the Ctrl+P picker). Only the TUI footer reliably
# shows what a pane is displaying:
#
#   agent main (main) | session tui-5b4f...7945d7 (openxr) | claude-opus-5-5 ...
#
# That line is the agent id, the session key relative to that agent, and the
# session label in parentheses when there is one. This is the only parser for it;
# the gateway plugin (index.js) and the resurrect save hook both call this
# script. If a future TUI changes the footer, parsing fails closed:
# the pane gets no state and keeps the command resurrect recorded.
#
# A key goes into a shell command on restore. Only keys made of [A-Za-z0-9:._-]
# are accepted. A footer truncated in a narrow pane fails that check too.
#
# Never exits non-zero.
#
#   tmux-session.sh panes           <pane id> TAB <session key> TAB <label>, one
#                                   line per pane running the OpenClaw TUI
#   tmux-session.sh --save <file>   @resurrect-hook-post-save-layout (through
#                                   ~/.tmux/resurrect-assistants.sh)

set -uo pipefail

command -v tmux >/dev/null 2>&1 || exit 0

readonly FOOTER='agent ([A-Za-z0-9_-]+)( \([^)]*\))? \| session ([^ ]+)( \(([^|]*)\))? \|'
readonly KEY='^agent:[A-Za-z0-9_-]+:[A-Za-z0-9:._-]+$'

# "<key> TAB <label>" for one pane, or nothing. The footer sits a few lines up
# from the bottom, under the chat log and above the editor. The last match on
# screen wins, so a footer quoted in the chat log above it is ignored.
pane_session() {
    local line agent session label='' key found=''
    while IFS= read -r line; do
        [[ "$line" =~ $FOOTER ]] || continue
        agent=${BASH_REMATCH[1]}
        session=${BASH_REMATCH[3]}
        label=${BASH_REMATCH[5]}
        found=1
    done < <(tmux capture-pane -p -t "$1" 2>/dev/null)
    [[ -n "$found" ]] || return 1
    if [[ "$session" == agent:* ]]; then key=$session; else key="agent:$agent:$session"; fi
    [[ "$key" =~ $KEY ]] || return 1
    label=${label//$'\t'/ }
    printf '%s\t%s\n' "$key" "$label"
}

# Every pane whose foreground process is the TUI. The launcher sets the process
# title to `openclaw`, which is what tmux reports as pane_current_command.
openclaw_panes() { # openclaw_panes <format>
    tmux list-panes -a -F "#{pane_current_command}"$'\t'"$1" 2>/dev/null |
        awk -F '\t' '$1 == "openclaw" { sub(/^[^\t]*\t/, ""); print }'
}

run_panes() {
    local pane found
    while IFS= read -r pane; do
        found=$(pane_session "$pane") || continue
        printf '%s\t%s\n' "$pane" "$found"
    done < <(openclaw_panes '#{pane_id}')
}

# resurrect records an OpenClaw pane as just `openclaw`. The launcher replaces
# the process's argv with its title, so a running TUI's `ps` shows no session.
# Rewrite each pane this script can place to `openclaw tui --session <key>`. The
# @resurrect-processes entry in ~/.tmux.conf is "openclaw tui". That matches the
# rewritten form, not the bare one, so a pane the hook could not place comes
# back as a shell instead of the TUI's last selected session.
run_save() {
    local file="$1" pane session window index found map=''
    [[ -f "$file" ]] || return 0
    while IFS=$'\t' read -r pane session window index; do
        found=$(pane_session "$pane") || continue
        map+="$session"$'\t'"$window"$'\t'"$index"$'\t'"${found%%$'\t'*}"$'\n'
    done < <(openclaw_panes '#{pane_id}'$'\t''#{session_name}'$'\t''#{window_index}'$'\t''#{pane_index}')
    [[ -n "$map" ]] || return 0

    # resurrect's pane line: $2 session, $3 window, $6 pane, $10 the command tmux
    # shows for the pane, $11 ":" and the command to restart.
    local tmp="$file.openclaw.$$"
    MAP="$map" awk '
        BEGIN {
            FS = OFS = "\t"
            n = split(ENVIRON["MAP"], row, "\n")
            for (i = 1; i < n; i++) {
                split(row[i], f, FS)
                key[f[1] FS f[2] FS f[3]] = f[4]
            }
        }
        $1 == "pane" && NF == 11 && $10 == "openclaw" && ($2 FS $3 FS $6) in key {
            $11 = ":openclaw tui --session " key[$2 FS $3 FS $6]
        }
        { print }
    ' "$file" > "$tmp" &&
        [[ "$(wc -l < "$tmp")" -eq "$(wc -l < "$file")" ]] &&
        mv -f "$tmp" "$file"
    rm -f "$tmp"
    return 0
}

case "${1:-}" in
    panes)  run_panes ;;
    --save) run_save "${2:-}" ;;
esac
exit 0
