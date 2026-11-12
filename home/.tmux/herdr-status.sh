#!/usr/bin/env bash
# herdr-status.sh -- show what herdr's agents are doing on the tmux tab herdr
# runs in, and name herdr's own tabs after the sessions in them.
#
# herdr runs its agents on its own server and starts them without TMUX or
# TMUX_PANE, so the tmux-status.sh hooks inside it have no tmux pane to paint.
# herdr does track every agent's state itself. This asks it once a second and
# hands the most urgent state in each herdr session to tmux-status.sh, with
# TMUX_PANE set to each tmux pane showing that session -- the same way the
# OpenClaw bridge drives it:
#
#   any agent blocked, or   -> blocked   red, static: something is waiting on you
#     a Claude turn ended
#     on a question
#   any agent working       -> working   the spinner
#   otherwise               -> idle      plain
#
# herdr's `done` (a turn finished that you have not looked at yet) stays plain
# on purpose, like a Claude tab whose turn has ended: only a dialog or a
# question reddens the tab. herdr has no state for a question asked in prose,
# so Claude's hooks report one as the pane token $asked (see `asked`). herdr's
# own sidebar still marks which agents are done.
#
# On the same pass, each herdr tab with an agent in it is renamed after the
# first agent there that has a name -- the name herdr's sidebar shows for it:
#
#   claude     its terminal title: the /rename name, or before one the topic
#              Claude Code makes up
#   opencode   the session title, which the herdr-session plugin in
#              ~/.config/opencode/plugins reports as the pane token $session
#   codex      its terminal title without the thread id ~/.tmux.conf strips
#              from window names; nothing while the title is only the id
#
# A tab renamed by hand is renamed back on the next pass; tabs without a named
# agent keep their label. Codex runs its hooks in a shared daemon that cannot
# tell which pane it is in, so its name is reported back to herdr from here,
# as $session, for the sidebar row in ~/.config/herdr/config.toml. Claude's is
# too, with a zero-width space (U+200B) in front while its turn has ended on a
# question, which that row turns red: herdr can colour a token only by its own
# text, so the mark has to be in the name, and this one takes no room on
# screen. The tab keeps the plain name. Needs jq; without it the tabs and
# sidebar names are left alone and the rest still works.
#
# Polling rather than herdr's event stream: status-change events are
# subscribed to one pane at a time, so following agents as they come and go
# would mean re-subscribing on every new pane, and a terminal title changing
# raises no event at all. `herdr agent list` answers in about a millisecond,
# and tmux and herdr are only touched when something has to change.
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
# Who reported a Codex or Claude pane's $session, as herdr records it.
readonly TOKEN_SOURCE=dotfiles:herdr-status

# A Claude agent whose turn ended on a question, as a jq condition on one
# agent. Its hooks report that as the pane token $asked (tmux-status.sh,
# report_asked). Only a Claude agent that is not working counts: a token a
# hook failed to clear cannot outlast the next turn, and one a killed Claude
# left on its pane -- pane metadata lives as long as the pane -- means nothing
# for an agent started there after it.
readonly ASKING='.agent == "claude" and .tokens.asked == "asked"
    and (.agent_status == "done" or .agent_status == "idle")'

# What to change in one session, from its agent list and its tab list. Prints
# three lines per change: `tab`, a tab id and its new label, or `token`, a
# Codex or Claude pane id and its sidebar name (empty to clear $session).
# Names lose control characters, so each is one line.
readonly NAMES="def asking: $ASKING;"'
def tidy: gsub("[[:cntrl:]]"; " ") | gsub("^ +| +$"; "");
def name:
  if .agent == "claude" then .terminal_title_stripped // ""
  elif .agent == "opencode" then .tokens.session // ""
  elif .agent == "codex" then
    (.terminal_title_stripped // "")
    | sub(" [|] [0-9a-f]+-[0-9a-f-]+[.][.][.].*$"; "")
    | if test("^[0-9a-f]+-[0-9a-f-]+[.][.][.]$") then "" else . end
  else "" end
  | tidy;
def mark: [8203] | implode;   # U+200B ZERO WIDTH SPACE
def sidebar: name as $n | if $n != "" and asking then mark + $n else $n end;
[inputs] as [$agents, $tabs]
| ($agents.result.agents // []) as $all
| ( $all[] | select(.agent == "codex" or .agent == "claude")
    | sidebar as $n | select($n != (.tokens.session // ""))
    | ["token", .pane_id, $n] ),
  ( $tabs.result.tabs[]? as $tab
    | first($all[] | select(.tab_id == $tab.tab_id) | name | select(. != "")) as $n
    | select($n != $tab.label)
    | ["tab", $tab.tab_id, $n] )
| .[]'

# A tmux server started from inside a herdr pane passes these on to its jobs,
# and herdr's CLI would then ask that pane's session instead of the one named.
unset HERDR_SOCKET_PATH HERDR_ENV HERDR_PANE_ID HERDR_TAB_ID HERDR_WORKSPACE_ID

# herdr's CLI for one session; `default` is the one a plain `herdr` asks.
#   hd <session> <args>...
hd() {
    local session="$1"
    shift
    if [[ "$session" == default ]]; then
        herdr "$@"
    else
        herdr --session "$session" "$@"
    fi
}

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

# Whether any Claude agent's turn ended on a question asked in prose (ASKING),
# which herdr itself sees as plain `done`. jq runs only when some agent
# carries the $asked token at all.
#   asked    reads AGENTS
asked() {
    [[ "$AGENTS" == *'"asked":"asked"'* ]] && (( HAVE_JQ )) || return 1
    jq -e "any(.result.agents[]?; $ASKING)" <<<"$AGENTS" >/dev/null 2>&1
}

# The most urgent state among a session's agents. Sets STATE, and AGENTS to
# the agent list for name_tabs. Fails when the session's server cannot be
# asked, so a hiccup leaves the tab as it was instead of flashing it plain.
#
# Substring tests rather than jq, as tmux-status.sh does for PreToolUse: herdr
# prints compact JSON, and inside a string value every quote is backslashed,
# so a terminal title cannot spell an unescaped "agent_status":"blocked".
#   query <session>
query() {
    STATE=''
    AGENTS="$(hd "$1" agent list 2>/dev/null)" || return 1
    [[ "$AGENTS" == *'"type":"agent_list"'* ]] || return 1
    if [[ "$AGENTS" == *'"agent_status":"blocked"'* ]] || asked; then
        STATE=blocked
    elif [[ "$AGENTS" == *'"agent_status":"working"'* ]]; then
        STATE=working
    else
        STATE=idle
    fi
}

# Renames a session's tabs after their agents, and reports Codex and Claude
# names back as $session for the sidebar, from the AGENTS query just read.
# Only what differs is sent.
#   name_tabs <session>
name_tabs() {
    local tabs kind id name
    (( HAVE_JQ )) || return 0
    [[ "$AGENTS" == *'"agent":'* ]] || return 0   # no agents, nothing to name
    tabs="$(hd "$1" tab list 2>/dev/null)" || return 0
    [[ "$tabs" == *'"type":"tab_list"'* ]] || return 0
    while IFS= read -r kind && IFS= read -r id && IFS= read -r name; do
        case "$kind" in
            # No `--`: herdr joins every word after the id into the label,
            # that one included.
            tab)
                hd "$1" tab rename "$id" "$name" ;;
            token)
                # `--token NAME=VALUE`, two words: herdr rejects --token=...
                if [[ -n "$name" ]]; then
                    hd "$1" pane report-metadata "$id" --source "$TOKEN_SOURCE" \
                        --token "session=$name"
                else
                    hd "$1" pane report-metadata "$id" --source "$TOKEN_SOURCE" \
                        --clear-token session
                fi
                ;;
        esac >/dev/null 2>&1
    done < <(printf '%s\n%s\n' "$AGENTS" "$tabs" | jq -rn "$NAMES" 2>/dev/null)
    return 0
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
                name_tabs "$SESSION"
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
HAVE_JQ=0
command -v jq >/dev/null 2>&1 && HAVE_JQ=1
readonly HAVE_JQ

case "${1:-}" in
    --run)  run_loop ;;
    --once) pass ;;
    '')     command -v setsid >/dev/null 2>&1 &&
                setsid -f "$SELF" --run </dev/null >/dev/null 2>&1 ;;
esac
exit 0
