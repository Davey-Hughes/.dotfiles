#!/usr/bin/env bash
#
# tmux-resurrect save hook: send every Claude Code pane back to its own
# conversation on restore.
#
# resurrect records a claude pane as just `claude`. Restarting that -- or
# `claude --continue`, which reopens the newest conversation in a directory --
# cannot tell apart two sessions working in the same project. Claude Code does
# keep a record of each live session in $CLAUDE_CONFIG_DIR/sessions/<pid>.json,
# session id included. So once resurrect has written its save file, this
# rewrites each claude pane's command in it to `claude --resume <session id>`,
# and ~/.tmux.conf lists claude in @resurrect-processes so resurrect types that
# back into the pane, the way it restores vim.
#
# It runs unattended on every continuum save, against a file that is only read
# after a crash. So anything short of certain leaves the pane as resurrect saved
# it, to come back as a shell rather than as the wrong conversation:
#
#   * the record must belong to the process. A session that died without
#     cleaning up leaves its record behind and its pid free for reuse, so the
#     start time has to match as well.
#   * the pane must be showing claude, not some program claude is running under.
#   * one session to a pane, or none.
#   * the conversation must be on disk. A session nobody has typed into yet has
#     no transcript, and `--resume` of it is an error.
#
# The session id ends up typed into a shell, so nothing but a UUID gets that far.
#
# Linux only: the start time comes from /proc. Elsewhere this does nothing, and
# claude panes restore as plain shells, as they did before it existed.
#
# Never exits non-zero, and touches the save file only by renaming a complete
# replacement over it.
#
# Usage:
#   resurrect-claude.sh <save file>      @resurrect-hook-post-save-layout

set -uo pipefail

file="${1:-}"
root="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
[[ -f "$file" && -d "$root/sessions" && -r /proc/self/stat ]] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

readonly UUID='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

# "<parent pid> <start time>" of a process. The command name in /proc/<pid>/stat
# can itself hold spaces and parentheses, so the fields are counted from its
# last ')'.
proc_stat() {
    local stat f
    { stat=$(< "/proc/$1/stat"); } 2>/dev/null || return 1
    read -ra f <<< "${stat##*) }"
    (( ${#f[@]} >= 20 )) || return 1
    printf '%s %s\n' "${f[1]}" "${f[19]}"
}

# The pane a process lives in, as "session<TAB>window<TAB>pane": the first of
# its ancestors that is a pane's own process.
pane_of() {
    local pid="$1" stat
    while (( pid > 1 )); do
        if [[ -n "${pane_at[$pid]:-}" ]]; then
            printf '%s\n' "${pane_at[$pid]}"
            return 0
        fi
        stat=$(proc_stat "$pid") || return 1
        pid=${stat% *}
    done
    return 1
}

declare -A pane_at=()       # a pane's process -> its address
while IFS=$'\t' read -r pid addr; do
    pane_at[$pid]=$addr
done < <(tmux list-panes -a \
    -F $'#{pane_pid}\t#{session_name}\t#{window_index}\t#{pane_index}' 2>/dev/null)

declare -A session_in=()    # pane address -> session id, emptied by a second claim
for record in "$root"/sessions/*.json; do
    IFS=$'\t' read -r pid start id < <(jq -r 'select(.kind == "interactive")
        | [.pid, .procStart, .sessionId] | @tsv' "$record" 2>/dev/null) || continue
    [[ "$pid" =~ ^[0-9]+$ && "$id" =~ $UUID ]] || continue
    stat=$(proc_stat "$pid") || continue
    [[ "${stat#* }" == "$start" ]] || continue
    addr=$(pane_of "$pid") || continue
    if [[ -n "${session_in[$addr]+claimed}" ]]; then
        session_in[$addr]=''
    else
        session_in[$addr]=$id
    fi
done

map=''
for addr in "${!session_in[@]}"; do
    id=${session_in[$addr]}
    [[ -n "$id" ]] || continue
    compgen -G "$root/projects/*/$id.jsonl" >/dev/null || continue
    map+="$addr"$'\t'"$id"$'\n'
done
[[ -n "$map" ]] || exit 0

# resurrect's pane line: $2 session, $3 window, $6 pane, $10 the command tmux
# shows for the pane, $11 ":" and the command to restart.
tmp="$file.claude.$$"
MAP="$map" awk '
    BEGIN {
        FS = OFS = "\t"
        n = split(ENVIRON["MAP"], row, "\n")
        for (i = 1; i < n; i++) {
            split(row[i], f, FS)
            id[f[1] FS f[2] FS f[3]] = f[4]
        }
    }
    $1 == "pane" && NF == 11 && $10 == "claude" && ($2 FS $3 FS $6) in id {
        $11 = ":claude --resume " id[$2 FS $3 FS $6]
    }
    { print }
' "$file" > "$tmp" &&
    [[ "$(wc -l < "$tmp")" -eq "$(wc -l < "$file")" ]] &&
    mv -f "$tmp" "$file"
rm -f "$tmp"
exit 0
