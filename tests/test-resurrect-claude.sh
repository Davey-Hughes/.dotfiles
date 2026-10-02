#!/usr/bin/env bash
# The resurrect-claude save hook sends every Claude Code pane back to its OWN
# conversation after a tmux restore, and leaves alone anything it is not sure of.
#
#     ./tests/test-resurrect-claude.sh
#
# Why this suite exists. `claude --continue` reopens the newest conversation in
# a directory, so two panes working in the same project would both come back as
# the same one. The hook instead rewrites each claude pane's saved command to
# `claude --resume <that pane's session id>`, read from Claude Code's own
# registry of live sessions. It edits tmux-resurrect's save file in place, and
# that file is only ever read after a crash -- the worst moment to discover the
# hook mangled it, or typed the wrong conversation's id into a pane.
#
# So this runs the real tmux-resurrect save and restore scripts against a
# throwaway tmux server on its own socket, with a stand-in `claude` that
# registers itself the way the real one does. $HOME, the resurrect directory and
# the Claude config directory all point into $TMPDIR, so a run never sees the
# live server, its save files or a real session.
#
# Not in run-all.sh: it needs the tmux-resurrect plugin, which tpm installs and
# this repo does not carry.
set -uo pipefail
# shellcheck source=tests/lib.sh
. "$(dirname "$0")/lib.sh"
cd_repo_root

REPO="$PWD"
HOOK="$REPO/home/.tmux/resurrect-claude.sh"
CONF="$REPO/home/.tmux.conf"
RESURRECT="$HOME/.tmux/plugins/tmux-resurrect/scripts"

need tmux "the resurrect-claude hook" || { summary "resurrect-claude"; exit $?; }
need jq   "the resurrect-claude hook" || { summary "resurrect-claude"; exit $?; }
if [ ! -r /proc/self/stat ]; then
  skip "the resurrect-claude hook -- no /proc, and the hook is a no-op without it"
  summary "resurrect-claude"; exit $?
fi
if [ ! -x "$RESURRECT/save.sh" ]; then
  skip "the resurrect-claude hook -- tmux-resurrect is not installed"
  summary "resurrect-claude"; exit $?
fi
if [ ! -x "$HOOK" ]; then
  fail "home/.tmux/resurrect-claude.sh is missing or not executable"
  summary "resurrect-claude"; exit $?
fi

TMP=$(mktemp -d)
SOCK="$TMP/s"
tm() { tmux -S "$SOCK" "$@"; }
trap 'tm kill-server 2>/dev/null; rm -rf "${TMP:?}"' EXIT

PROJ="$TMP/proj"            # every pane works here: the same-directory case
CC="$TMP/claude"            # stands in for $CLAUDE_CONFIG_DIR
RDIR="$TMP/resurrect"
LOG="$TMP/launches.log"
mkdir -p "$PROJ" "$CC/sessions" "$CC/projects/proj" "$RDIR" "$TMP/bin" "$TMP/home"
# The config names the hook by its stowed path, so give the fake $HOME that path.
ln -s "$REPO/home/.tmux" "$TMP/home/.tmux"

A=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa   # window 0, pane 0
B=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb   # window 0, pane 1 -- same directory as A
C=cccccccc-cccc-4ccc-8ccc-cccccccccccc   # window 1 -- nothing said yet, no transcript
D=dddddddd-dddd-4ddd-8ddd-dddddddddddd   # window 2 -- registry record is for another process
E=eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee   # window 3 -- claude underneath another program
F=ffffffff-ffff-4fff-8fff-ffffffffffff   # window 4 -- two sessions in one pane,
G=99999999-9999-4999-8999-999999999999   #             one of them in the background
for id in "$A" "$B" "$D" "$E" "$F" "$G"; do
  echo '{"type":"user"}' > "$CC/projects/proj/$id.jsonl"
done

# The stand-in. Like the real thing it writes sessions/<pid>.json holding its
# session id and its own start time, and it shows up to tmux as `claude`. It
# also logs every launch, which is how the restore half is observed.
cat > "$TMP/bin/claude" <<'EOF'
#!/usr/bin/env bash
args="$*"; id=''
while [ $# -gt 0 ]; do
  case "$1" in --session-id|--resume) id="$2"; shift ;; esac
  shift
done
stat=$(</proc/$$/stat); stat=${stat##*) }
# shellcheck disable=SC2086
set -- $stat
start=${20}
[ -n "${FAKE_STALE:-}" ] && start=1
printf '{"pid":%d,"sessionId":"%s","cwd":"%s","procStart":"%s","kind":"interactive"}\n' \
  "$$" "$id" "$PWD" "$start" > "$CLAUDE_CONFIG_DIR/sessions/$$.json"
printf '%s\t%s\t%s\n' \
  "$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}')" \
  "$args" "$PWD" >> "$LAUNCH_LOG"
exec -a claude sleep 600
EOF
chmod +x "$TMP/bin/claude"

# The two lines under test, lifted out of the real config rather than restated,
# so a typo there fails here.
grep -E '^set -g @resurrect-(processes|hook-post-save-layout) ' "$CONF" > "$TMP/resurrect.conf"

# tmux hands a new pane the PATH of whichever client asked for it, not the
# server's, so a pane this script creates would find the REAL claude and start
# it. Every pane shell therefore puts the stand-in first itself.
printf 'export PATH=%q:$PATH\n' "$TMP/bin" > "$TMP/rc"
PANE_SHELL="/bin/bash --noprofile --rcfile $TMP/rc"

# A stray `tmux` with no socket would otherwise reach the live server:
# TMUX_TMPDIR sends it to an empty directory instead.
start_server() { # start_server <session>
  env -u TMUX -u TMUX_PANE \
    HOME="$TMP/home" PATH="$TMP/bin:$PATH" TMUX_TMPDIR="$TMP" \
    XDG_DATA_HOME="$TMP/data" CLAUDE_CONFIG_DIR="$CC" LAUNCH_LOG="$LOG" \
    tmux -S "$SOCK" -f /dev/null new-session -d -s "$1" -x 120 -y 40 -c "$PROJ" \
      "$PANE_SHELL"
  tm set -g default-command "$PANE_SHELL"
  tm set -g automatic-rename off   # or window names drift between the two saves
  tm set -g @resurrect-dir "$RDIR"
  tm source-file "$TMP/resurrect.conf"
}

wait_for() { # wait_for <tenths of a second> <command...>
  local n="$1"; shift
  while [ "$n" -gt 0 ]; do
    "$@" && return 0
    sleep 0.1; n=$((n - 1))
  done
  return 1
}
registered() { [ "$(find "$CC/sessions" -name '*.json' | wc -l)" -eq "$1" ]; }
launched()   { [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -eq "$1" ]; }

save() { # save -> path of the save file `last` points at
  tm run-shell "$RESURRECT/save.sh quiet" >/dev/null 2>&1
  readlink -f "$RDIR/last"
}
command_of() { # command_of <file> <window> <pane> -> the saved command field
  awk -F'\t' -v w="$2" -v p="$3" \
    '$1 == "pane" && $2 == "main" && $3 == w && $6 == p { print $11 }' "$1"
}

# --- config -------------------------------------------------------------------
section "tmux.conf wires the hook up"
if [ "$(wc -l < "$TMP/resurrect.conf")" -eq 2 ]; then
  ok "home/.tmux.conf sets @resurrect-processes and @resurrect-hook-post-save-layout"
else
  fail "home/.tmux.conf should set @resurrect-processes and @resurrect-hook-post-save-layout once each"
  summary "resurrect-claude"; exit $?
fi

# --- save ---------------------------------------------------------------------
start_server main
tm split-window -t main:0 -c "$PROJ"
for _ in 1 2 3 4 5; do tm new-window -t main -c "$PROJ"; done
tm send-keys -t main:0.0 "claude --session-id $A" C-m
tm send-keys -t main:0.1 "claude --session-id $B" C-m
tm send-keys -t main:1.0 "claude --session-id $C" C-m
tm send-keys -t main:2.0 "FAKE_STALE=1 claude --session-id $D" C-m
tm send-keys -t main:3.0 "bash -c 'claude --session-id $E; :'" C-m
tm send-keys -t main:4.0 "claude --session-id $F & claude --session-id $G" C-m
tm send-keys -t main:5.0 "sleep 600" C-m

section "save"
if ! wait_for 100 registered 7 || ! launched 7; then
  fail "the stand-in claude sessions did not start (test rig, not the hook)"
  while IFS= read -r line; do note "$line"; done < <(tm capture-pane -p -t main:0.0 | grep .)
  summary "resurrect-claude"; exit $?
fi

# Save once with the hook off for a reference, then once with it on. Everything
# the hook does is the difference between the two files.
tm set -gu @resurrect-hook-post-save-layout
REF="$TMP/reference.txt"
cp "$(save)" "$REF"
tm source-file "$TMP/resurrect.conf"
sleep 1.1   # save files are named to the second
SAVED=$(save)

expect_command() { # expect_command <window> <pane> <expected> <what>
  local got; got=$(command_of "$SAVED" "$1" "$2")
  if [ "$got" = "$3" ]; then ok "$4"; else
    fail "$4"; note "window $1 pane $2: expected '$3'"; note "                  got '$got'"
  fi
}
unchanged() { # unchanged <window> <pane> <what>
  expect_command "$1" "$2" "$(command_of "$REF" "$1" "$2")" "$3"
}

expect_command 0 0 ":claude --resume $A" "a claude pane is saved as a resume of its own session"
expect_command 0 1 ":claude --resume $B" "a second claude pane in the same directory gets its own session, not the first one's"
unchanged 1 0 "a session with no transcript yet is left alone"
unchanged 2 0 "a registry record whose start time is not the live process's is left alone"
unchanged 3 0 "a claude running underneath another program is left alone"
unchanged 4 0 "a pane holding two sessions is left alone"

# Byte-for-byte: the reference with exactly those two fields replaced.
awk -F'\t' -v OFS='\t' -v a="$A" -v b="$B" '
  $1 == "pane" && $2 == "main" && $3 == 0 && $6 == 0 { $11 = ":claude --resume " a }
  $1 == "pane" && $2 == "main" && $3 == 0 && $6 == 1 { $11 = ":claude --resume " b }
  { print }' "$REF" > "$TMP/expected.txt"
if cmp -s "$TMP/expected.txt" "$SAVED"; then
  ok "nothing else in the save file changes"
else
  fail "nothing else in the save file changes"
  while IFS= read -r line; do note "$line"; done < <(diff "$TMP/expected.txt" "$SAVED")
fi

# Through run-shell, like resurrect's own call: run from here the hook would
# inherit this shell's $TMUX and read the live server's panes.
cp "$SAVED" "$TMP/again.txt"
tm run-shell "$HOOK $TMP/again.txt"
if cmp -s "$SAVED" "$TMP/again.txt"; then
  ok "running the hook on an already-rewritten file changes nothing"
else
  fail "running the hook on an already-rewritten file changes nothing"
fi

# --- restore ------------------------------------------------------------------
# The crash: the server dies and every session with it. Their registry records
# stay behind, as they do after a real one.
section "restore"
tm kill-server
wait_for 50 bash -c "! tmux -S '$SOCK' has-session 2>/dev/null"
rm -f "$LOG"
start_server boot
tm run-shell "$RESURRECT/restore.sh" >/dev/null 2>&1

# A, B, and the three unmapped stand-in panes (the real claude's saved command is
# a full path, which resurrect does not restart; the stand-in's is a bare `claude`).
wait_for 100 launched 5
want=$(printf 'main:0.0\t--resume %s\t%s\nmain:0.1\t--resume %s\t%s\n' "$A" "$PROJ" "$B" "$PROJ")
got=$(grep -e '--resume' "$LOG" 2>/dev/null | sort)
if [ "$got" = "$want" ]; then
  ok "each restored pane resumes its own session, in its own directory, and no other pane resumes anything"
else
  fail "each restored pane resumes its own session, in its own directory, and no other pane resumes anything"
  note "expected:"; while IFS= read -r line; do note "  $line"; done <<< "$want"
  note "got:";      while IFS= read -r line; do note "  $line"; done <<< "$got"
fi

summary "resurrect-claude"
