#!/usr/bin/env bash
# Adapt Codex lifecycle hooks to the shared Claude/shell tmux status renderer.
# Never emit a hook decision or block a tool because status rendering failed.
payload=$(cat 2>/dev/null)
event=$(jq -r '.hook_event_name // ""' <<<"$payload" 2>/dev/null)
# Register at turn boundaries, without scanning processes on every tool call.
case "$event" in
    SessionStart|UserPromptSubmit|Stop|SessionEnd)
        printf '%s' "$payload" | python3 "${CODEX_HOME:-$HOME/.codex}/hooks/tmux-session.py" >/dev/null 2>&1 ;;
esac
case "$event" in
    SessionStart)
        # Codex also emits SessionStart for children in the parent's pane.
        [[ $(jq -r '.agent_id // ""' <<<"$payload" 2>/dev/null) == "" ]] || exit 0
        # Compaction continues the current turn; keep its activity state.
        [[ $(jq -r '.source // ""' <<<"$payload" 2>/dev/null) != compact ]] || exit 0
        action=reset ;;
    UserPromptSubmit|PreToolUse|PostToolUse) action=working ;;
    PermissionRequest) action=blocked ;;
    Stop) action=idle ;;
    Interrupt) action=idle ;;
    SessionEnd) action=off ;;
    SubagentStart) action=subagent; set -- +1 ;;
    SubagentStop) action=subagent; set -- -1 ;;
    *) exit 0 ;;
esac
export TMUX_STATUS_CODEX=1
renderer="${XDG_CONFIG_HOME:-$HOME/.config}/.claude/hooks/tmux-status.sh"
[[ -r "$renderer" ]] || exit 0
printf '%s' "$payload" | bash "$renderer" "$action" "$@" >/dev/null 2>&1
exit 0
