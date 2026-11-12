#!/usr/bin/env bash
# herdr-status.sh -- show what herdr's agents are doing on the tmux tab herdr
# runs in.
#
# herdr runs its agents on its own server and starts them without TMUX or
# TMUX_PANE, so the tmux-status.sh hooks inside it have no tmux pane to paint.
# herdr does track every agent's state itself. This asks it once a second and
# hands the most urgent state in each herdr session to tmux-status.sh, with
# TMUX_PANE set to each tmux pane showing that session -- the same way the
# OpenClaw bridge drives it:
#
#   any agent blocked   -> blocked   red, static: a dialog is waiting on you
#   any agent working   -> working   the spinner
#   otherwise           -> idle      plain
#
# herdr's `done` (a turn finished that you have not looked at yet) stays plain
# on purpose, like a Claude tab whose turn has ended: only a dialog reddens the
# tab. herdr's own sidebar still marks which agents are done.
#
# Polling rather than herdr's event stream: status-change events are
# subscribed to one pane at a time, so following agents as they come and go
# would mean re-subscribing on every new pane. `herdr agent list` answers in
# about a millisecond, and tmux is only touched when a tab has to change.
#
# The pane option @herdr_session records which session a tmux pane was painted
# for. It is how a pane is recognised after herdr quits and leaves a shell
# there, so its state can be dropped.
#
# Linux only: the session a client shows is read from /proc.
#
#   herdr-status.sh          spawn the detached loop (idempotent); ~/.tmux.conf runs this
#   herdr-status.sh --run    internal: the loop itself
#   herdr-status.sh --once   one pass, no lock (tests)

set -uo pipefail

# Resolved, not as-invoked: this is reached through a stow symlink, and the
# mtime guard in run_loop must watch the script, not the link.
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
readonly SELF
# Through the repo rather than ~/.config/.claude: the suite runs this from a
# checkout, with no $HOME to lean on.
readonly STATUS="${SELF%/*}/../../config/.claude/hooks/tmux-status.sh"
readonly LOCK="${XDG_RUNTIME_DIR:-/tmp}/tmux-herdr-status.lock"
readonly TICK=1   # seconds between passes while a pane is running herdr
readonly IDLE=5   # ... and while none is

# A tmux server started from inside a herdr pane passes these on to its jobs,
# and herdr's CLI would then ask that pane's session instead of the one named.
unset HERDR_SOCKET_PATH HERDR_ENV HERDR_PANE_ID HERDR_TAB_ID HERDR_WORKSPACE_ID

# The herdr session the client in a pane shows, from the command line of the
# pane's foreground process: `herdr`, `herdr --session NAME`, or
# `herdr session attach NAME`. Fails for a client attached to another machine
# (--remote, --machine), which has no local server to ask. Sets SESSION.
#   client_session <pane_pid>
client_session() {
    local stat fg i
    local -a fields args
    SESSION=''
    { read -r stat <"/proc/$1/stat"; } 2>/dev/null || return 1
    # tpgid, the terminal's foreground process group, is field 8. Count from
    # after the comm field: it is parenthesised and may hold spaces.
    read -r -a fields <<<"${stat##*) }"
    fg="${fields[5]:-}"
    [[ "$fg" =~ ^[1-9][0-9]*$ ]] || return 1
    { mapfile -d '' -t args <"/proc/$fg/cmdline"; } 2>/dev/null || return 1
    SESSION=default
    for (( i = 1; i < ${#args[@]}; i++ )); do
        case "${args[i]}" in
            --session)   SESSION="${args[i + 1]:-}"; i=$(( i + 1 )) ;;
            --session=*) SESSION="${args[i]#--session=}" ;;
            --remote|--remote=*|--machine|--machine=*) SESSION=''; return 1 ;;
            session)
                if [[ "${args[i + 1]:-}" == attach ]]; then
                    SESSION="${args[i + 2]:-}"
                    i=$(( i + 2 ))
                fi
                ;;
        esac
    done
    [[ -n "$SESSION" ]]
}

# The most urgent state among a session's agents. Sets STATE. Fails when the
# session's server cannot be asked, so a hiccup leaves the tab as it was
# instead of flashing it plain.
#
# Substring tests rather than jq, as tmux-status.sh does for PreToolUse: herdr
# prints compact JSON, and inside a string value every quote is backslashed,
# so a terminal title cannot spell an unescaped "agent_status":"blocked".
#   query <session>
query() {
    local out
    STATE=''
    if [[ "$1" == default ]]; then
        out="$(herdr agent list 2>/dev/null)" || return 1
    else
        out="$(herdr --session "$1" agent list 2>/dev/null)" || return 1
    fi
    [[ "$out" == *'"type":"agent_list"'* ]] || return 1
    if [[ "$out" == *'"agent_status":"blocked"'* ]]; then
        STATE=blocked
    elif [[ "$out" == *'"agent_status":"working"'* ]]; then
        STATE=working
    else
        STATE=idle
    fi
}

# tmux-status.sh does the painting, so a herdr tab looks exactly like a Claude
# one. </dev/null: its `idle` and `working` read a hook event from stdin.
#   paint <pane> <verb>
paint() {
    TMUX_PANE="$1" bash "$STATUS" "$2" </dev/null >/dev/null 2>&1
}

# One look at every pane running herdr, plus every pane painted for herdr
# before. Sets SEEN when a pane is running herdr. Fails once tmux is gone.
pass() {
    local panes pane pid cmd state owner
    local -A states=() unreachable=()
    SEEN=0
    panes="$(tmux list-panes -a \
        -f '#{||:#{==:#{pane_current_command},herdr},#{@herdr_session}}' \
        -F '#{pane_id}|#{pane_pid}|#{pane_current_command}|#{@cc_state}|#{@herdr_session}' \
        2>/dev/null)" || return 1

    while IFS='|' read -r pane pid cmd state owner; do
        [[ -n "$pane" ]] || continue
        if [[ "$cmd" != herdr ]]; then
            # herdr quit and left a shell (or anything else) in this pane.
            paint "$pane" off
            tmux set-option -p -u -t "$pane" @herdr_session 2>/dev/null
            continue
        fi
        SEEN=1
        client_session "$pid" || continue

        # Ask each session once per pass, however many panes show it.
        if [[ -z "${states[$SESSION]+x}" && -z "${unreachable[$SESSION]+x}" ]]; then
            if query "$SESSION"; then
                states[$SESSION]="$STATE"
            else
                unreachable[$SESSION]=1
            fi
        fi
        [[ -n "${states[$SESSION]+x}" ]] || continue

        [[ "$owner" == "$SESSION" ]] ||
            tmux set-option -p -t "$pane" @herdr_session "$SESSION" 2>/dev/null
        # Compared against the pane rather than remembered here, so a pane
        # something else cleared is repainted on the next pass.
        [[ "$state" == "${states[$SESSION]}" ]] || paint "$pane" "${states[$SESSION]}"
    done <<<"$panes"
    return 0
}

run_loop() {
    exec 9>"$LOCK" || return 0
    flock -n 9 || return 0   # a loop is already live

    local born
    born="$(stat -c %Y "$SELF" 2>/dev/null)"
    while :; do
        # Edited on disk: hand over to a fresh copy, as status-right-daemon.sh
        # does, rather than keep polling with the old code until tmux restarts.
        if [[ "$(stat -c %Y "$SELF" 2>/dev/null)" != "$born" ]]; then
            exec 9>&-
            setsid -f "$SELF" --run </dev/null >/dev/null 2>&1
            return 0
        fi
        pass || return 0   # the tmux server is gone
        sleep "$(( SEEN ? TICK : IDLE ))"
    done
}

# ---------------------------------------------------------------------------

[[ -d /proc/self ]] || exit 0
command -v tmux >/dev/null 2>&1 || exit 0
command -v herdr >/dev/null 2>&1 || exit 0

case "${1:-}" in
    --run)  run_loop ;;
    --once) pass ;;
    '')     command -v setsid >/dev/null 2>&1 &&
                setsid -f "$SELF" --run </dev/null >/dev/null 2>&1 ;;
esac
exit 0
