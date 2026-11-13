#!/usr/bin/env bash
# statusline.sh -- Claude Code status line.
#
# Claude Code pipes the session's state to statusLine.command (settings.json)
# as JSON on stdin and shows the first line printed. Prints, for example:
#   Opus 5.5 · xhigh · $1.23 · 42% ctx 84k/200k · 94% cache · 1h05m · +120 -14 · wt:my-feature
# That is model, effort, session cost, context window used (as a share and as
# tokens of the window's size), prompt-cache hit rate, wall time since the
# session started, lines added and removed, and git worktree.
#
# Parts with nothing to say yet are left out: effort for models without one,
# the worktree outside a linked worktree, the cache rate and token count before
# the first response, lines until something changes. The context percentage
# turns yellow at 50% and red at 80%, ahead of auto-compact.
#
# The token count is context_window.total_input_tokens: input, cache reads and
# cache writes from the most recent response, the same sum used_percentage is
# taken from. Output tokens are not counted, matching the percentage.
#
# Claude Code draws the row with wrap "truncate": a line wider than the
# terminal loses its right end, never wraps to a second row. So the order
# here is also the order in which parts survive a narrow window.

# One field per line, so mapfile keeps an empty field as an empty element.
# hit_ratio is rounded, not floored: 0.29 * 100 is 28.999... in floating point.
# Token counts print as 950, 8.4k, 84k, 1M or 1.5M; zero prints nothing.
mapfile -t f < <(jq -r '
  def tokens:
    if   . <= 0   then ""
    elif . >= 1e6 then (. / 1e5 | round / 10 | tostring) + "M"
    elif . >= 1e4 then (. / 1e3 | round | tostring) + "k"
    elif . >= 1e3 then (. / 1e2 | round / 10 | tostring) + "k"
    else tostring
    end;
  .model.display_name // "?",
  .effort.level // "",
  .cost.total_cost_usd // 0,
  (.context_window.used_percentage // 0 | floor),
  (.context_window.total_input_tokens // 0 | tokens),
  (.context_window.context_window_size // 0 | tokens),
  (.prompt_cache.hit_ratio | if . == null then "" else . * 100 | round end),
  .cost.total_lines_added // 0,
  .cost.total_lines_removed // 0,
  ((.cost.total_duration_ms // 0) / 60000 | floor),
  .workspace.git_worktree // .worktree.name // ""
')
model=${f[0]} effort=${f[1]} cost=${f[2]} ctx=${f[3]}
ctx_used=${f[4]} ctx_size=${f[5]} cache=${f[6]} added=${f[7]}
removed=${f[8]} mins=${f[9]} worktree=${f[10]}

colour() { printf '\e[%sm%s\e[0m' "$1" "$2"; }

if   (( ctx >= 80 )); then ctx_colour=31
elif (( ctx >= 50 )); then ctx_colour=33
else                       ctx_colour=32
fi

ctx_part="$(colour "$ctx_colour" "$ctx%") ctx"
if [[ -n $ctx_used ]]; then
  ctx_part+=" $ctx_used${ctx_size:+/$ctx_size}"
fi

parts=("$model")
[[ -n $effort ]] && parts+=("$effort")
parts+=("$(printf '$%.2f' "$cost")")
parts+=("$ctx_part")
[[ -n $cache ]] && parts+=("$cache% cache")
if (( mins >= 60 )); then
  parts+=("$(printf '%dh%02dm' $((mins / 60)) $((mins % 60)))")
else
  parts+=("${mins}m")
fi
(( added || removed )) && parts+=("$(colour 32 "+$added") $(colour 31 "-$removed")")
[[ -n $worktree ]] && parts+=("$(colour 35 "wt:$worktree")")

sep=$'\e[2m · \e[0m'
line=${parts[0]}
for p in "${parts[@]:1}"; do line+=$sep$p; done
printf '%s\n' "$line"
