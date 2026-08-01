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
# Never exits non-zero: a PreToolUse hook returning 2 would block the tool call.
#
#   tmux-status.sh working|idle|blocked|reset|off   Claude Code hook events
#   tmux-status.sh subagent +1|-1                   SubagentStart / SubagentStop
#   tmux-status.sh shell-status <exit-code>         fish_postexec
#   tmux-status.sh next                             jump to a session wanting you
#   tmux-status.sh demo                             walk every state
#   tmux-status.sh --tick                           internal: the spinner loop

set -uo pipefail

readonly SELF="$(readlink -f "${BASH_SOURCE[0]}")"

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
readonly COL_FAIL='#db4b4b'      # tokyo-night error  -- last shell command failed

# Claude Code's own spinner, sampled off a live TUI: a star that swells and
# shrinks rather than rotating, so it reads as alive without pulling your eye.
# All five glyphs are single-width, so the tab label never shifts.
readonly SPIN=($'\u00b7' $'\u2722' $'\u2736' $'\u273b' \
               $'\u273d' $'\u273b' $'\u2736' $'\u2722')

readonly TICK=0.5                # seconds per frame; 8 frames = one 4s pulse
readonly IDLE_EXIT_TICKS=6       # ~3s of nothing working and the ticker quits
readonly LOCK="${XDG_RUNTIME_DIR:-/tmp}/tmux-cc-ticker.lock"

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
    for opt in @cc_state @cc_icon @cc_colour @cc_main @cc_subs; do
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

# Returns 0 when it spawned a ticker, 1 when one was already live.
start_ticker() {
    [[ -e "$LOCK" ]] || : >"$LOCK" 2>/dev/null
    # A held lock means a ticker is live. Two hooks can still race past this;
    # the flock inside run_ticker settles it.
    flock -n "$LOCK" true 2>/dev/null || return 1
    setsid -f "$SELF" --tick >/dev/null 2>&1 &
    return 0
}

# Derive what the tab should show from the two facts we track independently:
# whether the main loop is between UserPromptSubmit and Stop (@cc_main), and how
# many subagents are outstanding (@cc_subs). Keeping them separate is the whole
# point -- a backgrounded subagent can outlive the main turn's Stop, and the tab
# has to keep animating then, or it reads as "done, come look at me".
refresh_state() {
    local pane="$1" info main subs was oldcol col
    info="$(tmux display-message -p -t "$pane" \
        '#{@cc_main}|#{@cc_subs}|#{@cc_state}|#{@cc_colour}' 2>/dev/null)" || return 0
    IFS='|' read -r main subs was oldcol <<<"$info"
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
# actually up), then idle (its turn finished). Cycles from wherever you are, so
# repeated presses walk the whole set. Panes with no state are skipped -- those
# are sessions that have never fired a hook, not sessions waiting on you.
run_next() {
    local here targets sess win i found
    here="${TMUX_PANE:-$(tmux display-message -p '#{pane_id}' 2>/dev/null)}"

    mapfile -t targets < <(
        tmux list-panes -a -F '#{pane_id} #{@cc_state} #{pane_current_command}' 2>/dev/null |
        awk '$3=="claude" && $2=="blocked" {print $1}'
        tmux list-panes -a -F '#{pane_id} #{@cc_state} #{pane_current_command}' 2>/dev/null |
        awk '$3=="claude" && $2=="idle" {print $1}'
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

# These two run without a pane of their own: --tick is detached, and `next` is
# invoked from a tmux key binding, where TMUX_PANE is not guaranteed.
case "${1:-}" in
    --tick) run_ticker; exit 0 ;;
    next)   run_next;   exit 0 ;;
esac

PANE="${TMUX_PANE:-}"
[[ -z "$PANE" ]] && exit 0

case "${1:-}" in
    working)
        # UserPromptSubmit / PreToolUse / PostToolUse
        set_opt "$PANE" @cc_main busy
        refresh_state "$PANE"
        ;;
    idle)
        # Stop / StopFailure -- the main loop is done, but subagents may still
        # be running, so refresh_state decides whether that means idle.
        set_opt "$PANE" @cc_main done
        refresh_state "$PANE"
        ;;
    reset)
        # SessionStart -- a fresh session owns the pane; drop any stale counts.
        set_opt "$PANE" @cc_main done
        set_opt "$PANE" @cc_subs 0
        refresh_state "$PANE"
        ;;
    blocked)
        # Notification -- set directly; the next working/idle event supersedes it.
        # Same glyph as idle, so the tab bar stays visually uniform; red and the
        # absence of motion are what separate "wants you" from "nothing running".
        set_opt "$PANE" @cc_state blocked || exit 0
        set_opt "$PANE" @cc_colour ''
        set_opt "$PANE" @cc_icon "$(styled "$COL_BLOCK" "$ICON_IDLE")"
        redraw
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
