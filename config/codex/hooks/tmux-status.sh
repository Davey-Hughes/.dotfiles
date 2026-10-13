#!/usr/bin/env bash
# Adapt Codex lifecycle hooks to the shared Claude/shell tmux status renderer.
# Never emit a hook decision or block a tool because status rendering failed.
#
# Codex runs hooks in its shared app-server daemon, with the environment that
# daemon started in: TMUX_PANE names the pane Codex was first launched from,
# not the one showing this conversation. The pane is found by its title
# instead, where tui.terminal_title = ["thread", "thread-id"] puts the id.
payload=$(cat 2>/dev/null)
{ read -r event; read -r session; read -r agent; read -r source; } < <(
    jq -r '.hook_event_name // "", .session_id // "", .agent_id // "", .source // ""' \
        <<<"$payload" 2>/dev/null)
case "$event" in
    SessionStart)
        # Codex also emits SessionStart for children in the parent's pane.
        # Compaction continues the current turn; keep its activity state.
        action=reset; [[ -z "$agent" && "$source" != compact ]] || action= ;;
    UserPromptSubmit|PreToolUse|PostToolUse) action=working ;;
    PermissionRequest) action=blocked ;;
    Stop) action=idle ;;
    Interrupt) action=idle ;;
    SessionEnd) action=off ;;
    SubagentStart) action=subagent; set -- +1 ;;
    SubagentStop) action=subagent; set -- -1 ;;
    *) exit 0 ;;
esac
uuid='^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
[[ "$session" =~ $uuid ]] || exit 0
# Codex cuts the id in the title to 32 characters, the last three an ellipsis.
root="${session:0:29}"
child=; [[ "$agent" =~ $uuid ]] && child="${agent:0:29}"
# session_id is the root conversation's on every hook, a subagent's included,
# but with a subagent's thread on screen the title carries that thread's id.
# Hooks from the subagent bring it as agent_id; @cc_threads keeps the ones a
# pane has seen, so the root's own hooks still find the pane meanwhile.
panes=()
while IFS='|' read -r pane command had title; do
    [[ "$command" == codex ]] || continue
    threads="$root"; [[ "$had" == "$root"* ]] && threads="$had"
    [[ -z "$child" || " $threads " == *" $child "* ]] || threads+=" $child"
    [[ "$title" =~ ${threads// /|} ]] || continue
    panes+=("$pane")
    [[ "$threads" == "$had" ]] || tmux set-option -p -t "$pane" @cc_threads "$threads" 2>/dev/null
done < <(tmux list-panes -a -F \
    '#{pane_id}|#{pane_current_command}|#{@cc_threads}|#{pane_title}' 2>/dev/null)

export TMUX_STATUS_CODEX=1
renderer="${XDG_CONFIG_HOME:-$HOME/.config}/.claude/hooks/tmux-status.sh"
for TMUX_PANE in "${panes[@]}"; do
    export TMUX_PANE
    # Register at turn boundaries, without scanning processes on every tool call.
    case "$event" in
        SessionStart|UserPromptSubmit|Stop|SessionEnd)
            printf '%s' "$payload" | python3 "${CODEX_HOME:-$HOME/.codex}/hooks/tmux-session.py" >/dev/null 2>&1 ;;
    esac
    [[ -n "$action" && -r "$renderer" ]] || continue
    printf '%s' "$payload" | bash "$renderer" "$action" "$@" >/dev/null 2>&1
done
exit 0
