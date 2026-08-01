# Tint the tmux window icon red when the last command failed.
#
# "A command is running" needs no help: tmux renames the window after the
# running process, so powerkit already swaps in that command's icon. What it
# cannot show is how the command ended, which is what this adds.
#
# Shares its plumbing with the Claude Code activity indicator in
# ~/.config/.claude/hooks/tmux-status.sh -- see @sh_icon in ~/.tmux.conf.

status is-interactive; or return
set -q TMUX; or return

set -g __tmux_status_hook "$HOME/.config/.claude/hooks/tmux-status.sh"
test -x "$__tmux_status_hook"; or return
set -g __tmux_status_last 0

function __tmux_status_postexec --on-event fish_postexec
    # $status is still the exit status of the command that just ran.
    set -l code $status

    # Only touch tmux when the ok/failed state actually flips, so a run of
    # successful commands costs nothing.
    test $code -ne 0; and set code 1
    test $code -eq $__tmux_status_last; and return
    set -g __tmux_status_last $code

    command "$__tmux_status_hook" shell-status $code &
    disown
end
