#!/usr/bin/env bash
# tmux-status.sh -- surface Claude Code / shell activity in the tmux window icon.
#
# Claude Code hooks call `tmux-status.sh <event>`. The resolved state lands in
# the pane option @cc_state and the rendered glyph in @cc_icon, which
# ~/.tmux.conf hands to powerkit via
#   set -g @powerkit_window_command_icons 'claude=#{E:@cc_icon},fish=#{E:@sh_icon}'
# Pane-scoped user options resolve inside window-status-format and fall back to
# the global default, so each claude pane animates independently. The #{E:...}
# matters: it expands the option a second time, which is what lets the stored
# value carry a format instead of a fixed string (see styled()).
#
# status-interval is 5s, far too slow for a spinner, so while any pane is
# working a single detached ticker (--tick) advances the frame and forces a
# status redraw with `refresh-client -S`. It retires once nothing is working.
#
# Claude Code hands every hook its event as JSON on stdin. `working` reads it
# to catch the AskUserQuestion dialog going up (PreToolUse, tool_name), and
# `idle` reads it for the turn's last message (Stop, last_assistant_message):
# a turn that ends on a question paints red like a dialog would, instead of
# the plain idle glyph that says "done, nothing to see".
#
# The window is named after the session's /rename name, where it has one:
# this keeps it in the pane option @cc_name, and automatic-rename-format in
# ~/.tmux.conf reads it. No hook fires on a rename, but Claude Code retitles
# the pane, so ~/.tmux.conf runs `name` from pane-title-changed. A /clear
# keeps the name but not the title change, so SessionStart reads it back.
#
# Never exits non-zero: a PreToolUse hook returning 2 would block the tool call.
#
#   tmux-status.sh working|idle|reset|off     Claude Code hook events
#   tmux-status.sh blocked                    PermissionRequest / Notification (dialogs)
#   tmux-status.sh failed                     StopFailure
#   tmux-status.sh subagent +1|-1             SubagentStart / SubagentStop
#   tmux-status.sh shell-status <exit-code>   fish_postexec
#   tmux-status.sh next                       jump to a session wanting you
#   tmux-status.sh name <pane>                pane-title-changed: pick up a /rename
#   tmux-status.sh names                      every claude pane, at config load
#   tmux-status.sh demo                       walk every state
#   tmux-status.sh --tick                     internal: the spinner loop

set -uo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
readonly SELF

# Escapes, not literal glyphs. These are Private Use Area codepoints and they do
# not survive every editing path -- they were silently blanked twice while this
# was being written, each time leaving a coloured tab with no icon in it.
readonly ICON_IDLE=$'\uf069'   # nf-fa-asterisk   -- the stock claude icon
readonly ICON_SHELL=$'\uf489'  # nf-oct-terminal  -- the stock fish/bash icon

# colour216 (#ffaf87) is the exact SGR the Claude Code TUI emits for its own
# spinner, read off a live session, so the tab matches what is in the pane.
readonly COL_WORK='colour216'    # claude orange      -- claude is working
readonly COL_SUB='#bb9af7'       # tokyo-night purple -- ... waiting on subagents
# Deliberately not the tokyo-night yellow: next to the orange spinner it was
# too close to read at a glance, and this is the one state you must act on.
readonly COL_BLOCK='#f7768e'     # tokyo-night red    -- waiting on you
# Same red as a dialog on purpose: a turn that ended on "which one?" wants you
# exactly as much as a permission prompt does, and one colour keeps "red means
# go there" true. Change this alone to tell the two apart.
readonly COL_ASKED="$COL_BLOCK"  #                    -- turn ended on a question
readonly COL_FAIL='#db4b4b'      # tokyo-night error  -- last shell command failed

# Claude Code's own spinner, sampled off a live TUI: a star that swells and
# shrinks rather than rotating, so it reads as alive without pulling your eye.
# All five glyphs are single-width, so the tab label never shifts.
readonly SPIN=($'\u00b7' $'\u2722' $'\u2736' $'\u273b' \
               $'\u273d' $'\u273b' $'\u2736' $'\u2722')

# Each frame costs ~10 tmux calls (one set-option per working pane, plus a
# refresh per client), and every one of those invalidates the status line,
# which re-runs the #() jobs in status-right for each client. At 0.5s that
# measured ~36 powerkit-render spawns/sec against a status-interval of 5.
readonly TICK=1.0                # seconds per frame; 8 frames = one 8s pulse
readonly IDLE_EXIT_TICKS=3       # ~3s of nothing working and the ticker quits
readonly LOCK="${XDG_RUNTIME_DIR:-/tmp}/tmux-cc-ticker.lock"
# One pid file per live Claude Code session: its tmux pane, name, nameSource.
readonly SESSIONS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions"

# ---------------------------------------------------------------------------

# The status line only repaints on its own schedule; nudge every client instead.
redraw() {
    local client
    while read -r client; do
        [[ -n "$client" ]] && tmux refresh-client -S -t "$client" 2>/dev/null
    done < <(tmux list-clients -F '#{client_name}' 2>/dev/null)
    return 0
}

set_opt() { tmux set-option -p -t "$1" "$2" "$3" 2>/dev/null; }
unset_opt() { tmux set-option -p -u -t "$1" "$2" 2>/dev/null; }

clear_pane() {
    local pane="$1" opt
    for opt in @cc_state @cc_icon @cc_colour @cc_main @cc_subs @cc_asked @cc_name; do
        unset_opt "$pane" "$opt"
    done
}

# Colour a glyph, but only on tabs you are not currently looking at.
#
# The active tab's background is #9d7cd8; every state colour lands between 1.26
# and 1.86 contrast against it, versus 3.7-5.5 on the inactive #3b4261. Rather
# than wash the hues out to near-white to fix that, drop the colour on the one
# window a client is displaying -- you do not need a tab to tell you about the
# session already on your screen, and the glyph still animates there.
#
# Emitted as a tmux format so the choice happens per window at render time;
# ~/.tmux.conf reads these options through #{E:...} to expand it.
styled() {
    printf '#{?#{&&:#{window_active},#{session_attached}},,#[fg=%s]}%s' "$1" "$2"
}

# Paint "wants you": coloured, no motion. Same glyph as idle, so the tab bar
# stays visually uniform -- the colour and the absence of motion are what
# separate "wants you" from "nothing running".
#   mark_wanted <pane> <state> <colour>
mark_wanted() {
    set_opt "$1" @cc_state "$2" || return 1
    set_opt "$1" @cc_colour ''
    set_opt "$1" @cc_icon "$(styled "$3" "$ICON_IDLE")"
    redraw
}
mark_blocked() { mark_wanted "$1" blocked "$COL_BLOCK"; }

# The hook event Claude Code piped in, if any. Only read when stdin is really
# a pipe: run by hand from a terminal there is nothing to read, and cat would
# sit waiting on the keyboard.
HOOK_JSON=''
read_hook_input() {
    [[ -t 0 ]] && return 0
    HOOK_JSON="$(cat 2>/dev/null)"
    return 0
}

# PreToolUse for the AskUserQuestion dialog. Claude Code serialises the event
# with JSON.stringify, so the keys are exact and unspaced, and a quote inside a
# string value is always backslashed -- this cannot match inside tool_input.
# A substring test rather than jq because this runs on every tool call.
dialog_going_up() {
    [[ "$HOOK_JSON" == *'"hook_event_name":"PreToolUse"'* &&
       "$HOOK_JSON" == *'"tool_name":"AskUserQuestion"'* ]]
}

# True when the turn's final message ends on a question mark. Looks at the
# last non-blank line only, after stripping the markdown that tends to wrap a
# closing question -- "**Which approach?**" counts, "Is it? I think so." does
# not. Stop fires once a turn, so jq is affordable here.
asked_question() {
    [[ -n "$HOOK_JSON" ]] && command -v jq >/dev/null 2>&1 || return 1
    jq -e '(.last_assistant_message // "") | split("\n")
           | map(gsub("\\s+$"; "")) | map(select(length > 0)) | (last // "")
           | sub("[\\s*_)\\]\"]+$"; "") | endswith("?")' \
        <<<"$HOOK_JSON" >/dev/null 2>&1
}

# Returns 0 when it spawned a ticker, 1 when one was already live.
start_ticker() {
    [[ -e "$LOCK" ]] || : >"$LOCK" 2>/dev/null
    # A held lock means a ticker is live. Two hooks can still race past this;
    # the flock inside run_ticker settles it.
    flock -n "$LOCK" true 2>/dev/null || return 1
    # </dev/null so the ticker does not inherit the hook's stdin pipe.
    setsid -f "$SELF" --tick </dev/null >/dev/null 2>&1 &
    return 0
}

# Derive what the tab should show from the two facts we track independently:
# whether the main loop is between UserPromptSubmit and Stop (@cc_main), and how
# many subagents are outstanding (@cc_subs). Keeping them separate is the whole
# point -- a backgrounded subagent can outlive the main turn's Stop, and the tab
# has to keep animating then, or it reads as "done, come look at me".
refresh_state() {
    local pane="$1" info main subs was oldcol asked col
    info="$(tmux display-message -p -t "$pane" \
        '#{@cc_main}|#{@cc_subs}|#{@cc_state}|#{@cc_colour}|#{@cc_asked}' 2>/dev/null)" || return 0
    IFS='|' read -r main subs was oldcol asked <<<"$info"
    [[ "$subs" =~ ^[0-9]+$ ]] || subs=0

    if [[ "$main" == busy ]] || (( subs > 0 )); then
        # Purple whenever subagents are outstanding, so "waiting on my own
        # subagents" is distinguishable from "the main loop is chewing".
        col="$COL_WORK"
        (( subs > 0 )) && col="$COL_SUB"

        [[ "$was" == working ]] || set_opt "$pane" @cc_state working
        [[ "$oldcol" == "$col" ]] || set_opt "$pane" @cc_colour "$col"

        # Repaint on entry, on a colour change, and whenever we had to spawn a
        # ticker -- the previous one may have retired and left a stale frame.
        # Already working with a live ticker and no colour change is the common
        # case (every PreToolUse/PostToolUse) and does nothing.
        if start_ticker || [[ "$was" != working ]] || [[ "$oldcol" != "$col" ]]; then
            set_opt "$pane" @cc_icon "$(styled "$col" "${SPIN[0]}")"
            redraw
        fi
    elif [[ -n "$asked" ]]; then
        # The turn ended on a question (see `idle`). Red, static: a session
        # waiting on your answer, whether or not a dialog is drawing it.
        [[ "$was" == asked ]] && return 0
        mark_wanted "$pane" asked "$COL_ASKED"
    else
        [[ "$was" == idle ]] && return 0
        set_opt "$pane" @cc_state idle
        set_opt "$pane" @cc_colour ''
        set_opt "$pane" @cc_icon "$ICON_IDLE"
        redraw
    fi
    return 0
}

run_ticker() {
    exec 9>"$LOCK" || return 0
    flock -n 9 || return 0

    # Retire if the script changes on disk. Without this, a ticker started
    # before an edit keeps repainting @cc_icon from its old in-memory frames,
    # and the edit looks like it did nothing.
    local born; born="$(stat -c %Y "$SELF" 2>/dev/null)"

    local frame=0 quiet=0 working pane state colour cmd
    while :; do
        [[ "$(stat -c %Y "$SELF" 2>/dev/null)" == "$born" ]] || break
        working=0
        # A dead tmux server makes this yield nothing, which retires the ticker.
        while read -r pane state colour cmd; do
            [[ "$state" == working ]] || continue
            # claude is no longer the pane's process: it exited or crashed
            # without SessionEnd, or the pane was reused. Drop the stale state
            # rather than animate a tab nobody is working in.
            if [[ "$cmd" != claude ]]; then
                clear_pane "$pane"
                continue
            fi
            working=1
            set_opt "$pane" @cc_icon \
                "$(styled "${colour:-$COL_WORK}" "${SPIN[frame % ${#SPIN[@]}]}")"
        done < <(tmux list-panes -a -F \
            '#{pane_id} #{@cc_state} #{@cc_colour} #{pane_current_command}' 2>/dev/null)

        if (( working )); then
            quiet=0
            redraw
        elif (( ++quiet >= IDLE_EXIT_TICKS )); then
            break
        fi

        sleep "$TICK"
        (( frame++ ))
    done
    return 0
}

# Jump to the next claude session that wants you: blocked first (a prompt is
# actually up, and a turn is stalled behind it), then asked (its turn ended on
# a question), then idle (its turn finished). Cycles from wherever you are, so
# repeated presses walk the whole set. Panes with no state are skipped -- those
# are sessions that have never fired a hook, not sessions waiting on you.
run_next() {
    local here targets sess win i found state
    here="${TMUX_PANE:-$(tmux display-message -p '#{pane_id}' 2>/dev/null)}"

    mapfile -t targets < <(
        for state in blocked asked idle; do
            tmux list-panes -a -F '#{pane_id} #{@cc_state} #{pane_current_command}' 2>/dev/null |
            awk -v s="$state" '$3=="claude" && $2==s {print $1}'
        done
    )

    if (( ${#targets[@]} == 0 )); then
        tmux display-message "no claude session is waiting on you"
        return 0
    fi

    # Start after the current pane so a second press advances instead of
    # bouncing back to the same window.
    found=0
    for i in "${!targets[@]}"; do
        if [[ "${targets[i]}" == "$here" ]]; then
            found=$(( (i + 1) % ${#targets[@]} ))
            break
        fi
    done

    local target="${targets[found]}"
    read -r sess win <<<"$(tmux display-message -p -t "$target" \
        '#{session_name} #{window_index}' 2>/dev/null)"
    [[ -z "$sess" ]] && return 0
    tmux switch-client -t "$sess" 2>/dev/null
    tmux select-window -t "$sess:$win" 2>/dev/null
    return 0
}

# The pane's session name if you gave it one with /rename, else nothing.
# nameSource "derived" is the auto-generated slug (dotfiles-5b), which says no
# more than "claude" does. A crashed session leaves its pid file behind and
# pane ids get reused, so the newest file with a live pid wins.
session_name() {
    local pane="$1" pid name
    command -v jq >/dev/null 2>&1 || return 0
    while IFS=$'\t' read -r _ pid name; do
        kill -0 "$pid" 2>/dev/null || continue
        printf '%s' "$name"
        return 0
    done < <(jq -r --arg p ".$pane" '
        select((.tmux // "") | endswith($p))
        | [(.startedAt // 0), .pid, (if .nameSource == "user" then .name else "" end)]
        | @tsv' "$SESSIONS"/*.json 2>/dev/null | sort -rn)
    return 0
}

# Returns 0 when @cc_name changed, so callers only rename when it did.
sync_name() {
    local pane="$1" name had
    name="$(session_name "$pane")"
    had="$(tmux display-message -p -t "$pane" '#{@cc_name}' 2>/dev/null)"
    [[ "$name" == "$had" ]] && return 1
    if [[ -n "$name" ]]; then
        set_opt "$pane" @cc_name "$name"
    else
        unset_opt "$pane" @cc_name
    fi
}

# tmux only re-reads automatic-rename-format once the pane prints something,
# and an idle claude pane prints nothing, so a new @cc_name would sit unseen.
# Setting automatic-rename -- even to the value it has -- marks every window
# that has it on for a fresh look. Left alone if you turned it off globally.
rename_now() {
    [[ "$(tmux show-options -gv automatic-rename 2>/dev/null)" == on ]] &&
        tmux set-option -g automatic-rename on 2>/dev/null
    return 0
}

# Claude Code writes the new name to its pid file and retitles the pane, in no
# promised order. Look again a moment later rather than trust the first read.
run_name() {
    local pane="$1"
    [[ -n "$pane" ]] || return 0
    sync_name "$pane" && rename_now
    sleep 1
    sync_name "$pane" && rename_now
    return 0
}

run_names() {
    local pane changed=0
    while read -r pane; do
        sync_name "$pane" && changed=1
    done < <(tmux list-panes -a -F '#{pane_id} #{pane_current_command}' 2>/dev/null |
             awk '$2=="claude" {print $1}')
    (( changed )) && rename_now
    return 0
}

# Walks every state slowly enough to watch, then restores what was there.
run_demo() {
    local pane="${TMUX_PANE:-}" main subs
    [[ -z "$pane" ]] && return 0
    main="$(tmux display-message -p -t "$pane" '#{@cc_main}' 2>/dev/null)"
    subs="$(tmux display-message -p -t "$pane" '#{@cc_subs}' 2>/dev/null)"

    echo "NOTE: colours only show on tabs you are not looking at -- watch a"
    echo "      different window's tab, or switch away, to see them."
    echo
    echo "idle          ${ICON_IDLE}  plain, no motion                  (3s)"
    "$SELF" reset; sleep 3
    echo "working       ${SPIN[4]}  orange star, slow pulse           (9s)"
    "$SELF" working; sleep 9
    echo "on subagents  ${SPIN[4]}  same pulse, purple                (9s)"
    "$SELF" subagent +1; sleep 9
    echo "turn ended, subagent still up -- stays purple, not idle     (6s)"
    "$SELF" idle; sleep 6
    "$SELF" subagent -1
    echo "wants you     ${ICON_IDLE}  red, no motion                    (4s)"
    "$SELF" blocked; sleep 4
    echo "asked you     ${ICON_IDLE}  same red -- turn ended on a question (4s)"
    printf '%s' '{"last_assistant_message":"Which one?"}' | "$SELF" idle; sleep 4
    echo "shell failed  ${ICON_SHELL}  red, on fish panes                (4s)"
    "$SELF" shell-status 1; sleep 4
    "$SELF" shell-status 0

    set_opt "$pane" @cc_main "${main:-done}"
    set_opt "$pane" @cc_subs "${subs:-0}"
    refresh_state "$pane"
    echo "restored"
    return 0
}

# ---------------------------------------------------------------------------

command -v tmux >/dev/null 2>&1 || exit 0

# These run without a pane of their own: --tick is detached, and the rest are
# invoked from tmux, where TMUX_PANE is not guaranteed.
case "${1:-}" in
    --tick) run_ticker;          exit 0 ;;
    next)   run_next;            exit 0 ;;
    name)   run_name "${2:-}";   exit 0 ;;
    names)  run_names;           exit 0 ;;
esac

PANE="${TMUX_PANE:-}"
[[ -z "$PANE" ]] && exit 0

case "${1:-}" in
    working)
        # UserPromptSubmit / PreToolUse / PostToolUse / PostToolUseFailure.
        # Any of these means the last question has been answered, so drop it.
        read_hook_input
        set_opt "$PANE" @cc_main busy
        unset_opt "$PANE" @cc_asked
        if dialog_going_up; then
            # The AskUserQuestion dialog. Notification would redden this too,
            # but as permission_prompt, which Claude Code only sends once the
            # dialog has sat unanswered for ~6s. Paint it the moment it opens.
            # PostToolUse does not fire for this tool, so the red holds until
            # the next tool call or Stop -- the same as it does today.
            mark_blocked "$PANE" || exit 0
        else
            refresh_state "$PANE"
        fi
        ;;
    idle)
        # Stop -- the main loop is done, but subagents may still be running,
        # so refresh_state decides whether that means idle. If the turn ended
        # on a question, flag it: refresh_state paints that red instead of the
        # plain glyph, and the flag outlives a subagent finishing afterwards.
        # No hook fires for a question asked in prose, only for dialogs, so
        # the message text is the only signal there is.
        read_hook_input
        set_opt "$PANE" @cc_main 'done'
        if asked_question; then
            set_opt "$PANE" @cc_asked 1
        else
            unset_opt "$PANE" @cc_asked
        fi
        refresh_state "$PANE"
        ;;
    reset)
        # SessionStart -- a fresh session owns the pane; drop any stale counts.
        # A /clear also ran SessionEnd, whose `off` dropped @cc_name, but the
        # name outlives it: the pid file keeps it, and the pane title does not
        # change, so pane-title-changed never fires to put it back. Read it
        # back here.
        set_opt "$PANE" @cc_main 'done'
        set_opt "$PANE" @cc_subs 0
        unset_opt "$PANE" @cc_asked
        sync_name "$PANE" && rename_now
        refresh_state "$PANE"
        ;;
    blocked)
        # PermissionRequest, plus Notification narrowed in settings.json to the
        # types where a dialog is actually on screen.
        #
        # PermissionRequest fires the instant Claude Code is about to ask you
        # for permission. It is also a decision hook: JSON on stdout would allow
        # or deny the tool, so this verb must print nothing. Silence and exit 0
        # mean "no opinion", and the dialog opens as it always did.
        #
        # Notification covers what PermissionRequest does not (MCP elicitation
        # dialogs, agent_needs_input) and doubles as a fallback for permission
        # prompts, though Claude Code only sends permission_prompt once the
        # dialog has sat unanswered for ~6s. It fires for twelve things, most of
        # which are informational -- auth_success, agent_completed, the quota
        # auto-resume trio -- and elicitation_response/_complete fire the instant
        # you answer a prompt, so an unfiltered hook reddens the tab exactly when
        # you have just unblocked it.
        #
        # @cc_main is deliberately left alone: the turn is still running, and the
        # next working/idle event supersedes this.
        mark_blocked "$PANE" || exit 0
        ;;
    failed)
        # StopFailure -- the turn died on an API error (rate_limit, overloaded,
        # billing_error...). Unlike `blocked` the main loop really has ended, so
        # clear @cc_main: nothing else will fire a working/idle event for it, and
        # leaving it busy would make the next refresh_state re-animate a dead
        # turn. Red because a crashed turn needs you; plain idle made it
        # indistinguishable from one that finished cleanly.
        #
        # A subagent still outstanding here will drop this back to plain idle on
        # its SubagentStop. Rare, and losing the red beats the machinery to hold
        # it.
        set_opt "$PANE" @cc_main 'done'
        mark_blocked "$PANE" || exit 0
        ;;
    subagent)
        # display-message resolves the option with inheritance and prints empty
        # when it was never set, which `show-option -v` does not.
        n="$(tmux display-message -p -t "$PANE" '#{@cc_subs}' 2>/dev/null)"
        [[ "$n" =~ ^[0-9]+$ ]] || n=0
        case "${2:-}" in
            +1) n=$(( n + 1 )) ;;
            -1) (( n > 0 )) && n=$(( n - 1 )) ;;
        esac
        set_opt "$PANE" @cc_subs "$n"
        refresh_state "$PANE"
        ;;
    off)
        # SessionEnd
        clear_pane "$PANE"
        redraw
        ;;
    shell-status)
        # fish_postexec, with the exit status of the command that just ran.
        if [[ "${2:-0}" == 0 ]]; then
            unset_opt "$PANE" @sh_icon
        else
            set_opt "$PANE" @sh_icon "$(styled "$COL_FAIL" "$ICON_SHELL")"
        fi
        redraw
        ;;
    demo)
        run_demo
        ;;
esac

exit 0
