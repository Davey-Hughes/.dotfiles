#!/usr/bin/env bash
#
# Push-model renderer for status-right.
#
# tmux re-runs every #() in the status line on each *redraw*, per client -- not
# on status-interval. With a handful of active panes that measured 60-85
# powerkit-render spawns/sec against a status-interval of 5, and those spawns
# were 99% of the tmux server's read syscalls (22.9k/sec -> 286/sec once the
# jobs were removed) and ~38% of its CPU.
#
# So nothing shells out from the status line any more. This daemon renders on a
# fixed interval and parks the result in a tmux option; status-right just reads
# that option back. Redraws then cost an option lookup instead of two forks.
#
# Why the option indirection rather than writing the rendered text straight into
# status-right: #{@opt} is *not* re-expanded as a format, so if a plugin ever
# emits a #{...} sequence it stays literal instead of being evaluated. Styles
# still work -- #[fg=...] is interpreted when the status line is drawn, which
# happens after expansion either way (verified: the option form emits a real
# SGR escape, not literal text).
#
# Usage:
#   status-right-daemon.sh            spawn a detached daemon (idempotent)
#   status-right-daemon.sh --run      internal: the loop itself

set -uo pipefail

readonly SELF="${BASH_SOURCE[0]}"
readonly RENDER="$HOME/projects/tmux-powerkit/bin/powerkit-render"
readonly CONTINUUM="$HOME/.tmux/plugins/tmux-continuum/scripts/continuum_save.sh"

readonly INTERVAL=5              # seconds; matches status-interval
readonly OPT='@pk_status_right'  # where the rendered text is parked
readonly LOCK="${XDG_RUNTIME_DIR:-/tmp}/tmux-status-right.lock"

# What status-right must contain for the push model to be live.
readonly WANT="#{${OPT}}"

# If this daemon stops, status-right would read an option nobody updates any
# more and the right side of the bar would freeze or blank. Fall back to the
# original #() form so a crash degrades to the old (slower) behaviour instead
# of a broken status line.
readonly FALLBACK="#(${CONTINUUM})#(${RENDER} right)"

alive() { tmux has-session 2>/dev/null; }

restore() {
    alive || return 0
    tmux set-option -g status-right "$FALLBACK" 2>/dev/null || true
}

run_loop() {
    exec 9>"$LOCK" || return 0
    flock -n 9 || return 0        # a daemon is already live

    # Retire if this file changes on disk, so an edit takes effect on the next
    # tmux reload rather than leaving a stale loop running the old logic.
    local born; born="$(stat -c %Y "$SELF" 2>/dev/null)"

    trap restore EXIT

    local rendered previous='' current
    while :; do
        alive || exit 0
        [[ "$(stat -c %Y "$SELF" 2>/dev/null)" == "$born" ]] || exit 0

        # continuum lived in status-right purely to get called periodically --
        # it emits nothing. It self-throttles internally, so calling it every
        # cycle is safe and keeps session saving working.
        [[ -x "$CONTINUUM" ]] && "$CONTINUUM" >/dev/null 2>&1

        rendered="$("$RENDER" right 2>/dev/null)"

        # Never publish an empty render: a failed or half-initialised powerkit
        # would otherwise blank the right side of the bar until the next cycle.
        if [[ -n "$rendered" && "$rendered" != "$previous" ]]; then
            tmux set-option -g "$OPT" "$rendered" 2>/dev/null && previous="$rendered"
        fi

        # Re-assert status-right only when something else has clobbered it
        # (a config reload re-runs powerkit, which sets its own #() form).
        # Setting it unconditionally would dirty the status line every cycle.
        current="$(tmux show-options -gv status-right 2>/dev/null)"
        [[ "$current" == "$WANT" ]] || tmux set-option -g status-right "$WANT" 2>/dev/null

        sleep "$INTERVAL"
    done
}

case "${1:-}" in
    --run) run_loop ;;
    *)
        # Detach so the daemon outlives the `run-shell -b` that started it.
        [[ -e "$LOCK" ]] || : >"$LOCK" 2>/dev/null
        setsid -f "$SELF" --run >/dev/null 2>&1 &
        ;;
esac
