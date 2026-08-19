#!/usr/bin/env bash
#
# Bound the CI caches on this box, mirroring prune_ci_caches on DaveyNet.
#
# Scheduling here is a systemd USER timer owned by cirunner rather than a root cron: lingering is
# already enabled, the unit then runs as the user that owns the rootless daemon, and no privileged
# component ever touches the CI caches.
#
# Also repairs /usr/local/bin/ci-runner, which could never match the NOPASSWD sudoers rule.

set -euo pipefail

RUNNER_USER=cirunner
RUNNER_HOME=/home/$RUNNER_USER
RUID=$(id -u "$RUNNER_USER")
SOCK="/run/user/$RUID/docker.sock"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

as_runner() {
  sudo -u "$RUNNER_USER" \
    XDG_RUNTIME_DIR="/run/user/$RUID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$RUID/bus" \
    PATH=/usr/bin:/bin "$@"
}

echo "=== 1. prune script ==="
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 755 "$RUNNER_HOME/.local/bin"

# Quoted heredoc: nothing below is expanded here. The script resolves its own paths at runtime
# from $HOME and $(id -u), so it works unchanged for any runner user.
cat > "$RUNNER_HOME/.local/bin/prune-ci-caches" <<'PRUNE_EOF'
#!/usr/bin/env bash
#
# Bound this host's two CI caches. Runs as the runner user against its ROOTLESS daemon.
#
#   1. BuildKit build cache — one volume per buildx builder. buildkitd self-GCs, but its default
#      ceiling is "Reserved 10GB, Free 382GB, Maximum 100GB" PER BUILDER — effectively unbounded.
#   2. actions/cache store — bolt.db (key -> blob id) + cache/<shard>/<id> blobs. The runner GCs
#      by AGE (7d unused / 30d used) but never by SIZE.
#
# Env overrides: DRY_RUN, ACTCACHE_LIMIT_GB, WAIT_MAX_SEC, DEFAULT_CAP_MB

set -uo pipefail

RUID=$(id -u)
export DOCKER_HOST=${DOCKER_HOST:-unix:///run/user/$RUID/docker.sock}

ACTCACHE_DIR=${ACTCACHE_DIR:-$HOME/.cache/forgejo-runner/actcache}
STATE_FILE=${STATE_FILE:-$HOME/.local/share/forgejo-runner/.prune-state}
RUNNER=${RUNNER:-forgejo-runner}

# 40 GB rather than DaveyNet's 25: this box has ~3 TB free, and a bigger cache means faster CI.
# The point here is a ceiling, not thrift.
ACTCACHE_LIMIT_GB=${ACTCACHE_LIMIT_GB:-40}
DEFAULT_CAP_MB=${DEFAULT_CAP_MB:-15000}
WAIT_MAX_SEC=${WAIT_MAX_SEC:-900}
POLL_SEC=${POLL_SEC:-30}
DRY_RUN=${DRY_RUN:-0}

declare -A CAP_MB=(
  [buildx_buildkit_wud-safe0]=40000
  [buildx_buildkit_redextape-wud0]=20000
  [buildx_buildkit_pte-ci0]=10000
  [buildx_buildkit_pf-ci0]=10000
  [buildx_buildkit_studiojus10-wud0]=10000
)

say() { printf '%s\n' "$*"; }
run() { if [ "$DRY_RUN" = 1 ]; then say "  [dry-run] $*"; else "$@"; fi; }
ci_busy() { docker ps --format '{{.Names}}' | grep -q '^FORGEJO-ACTIONS-TASK-'; }
cache_total() { docker exec "$1" buildctl du 2>/dev/null | awk '/^Total:/{print $2}'; }

say "=== $(date '+%F %T') — CI cache trim ($(hostname)) ==="
say

# buildctl prune is safe during an active build: BuildKit locks in-use records and skips them.
say "--- BuildKit builders ---"
builders=$(docker ps --format '{{.Names}}' | grep '^buildx_buildkit_' | sort)
if [ -z "$builders" ]; then
  say "  none running (created on demand by CI)"
else
  for c in $builders; do
    cap=${CAP_MB[$c]:-$DEFAULT_CAP_MB}
    before=$(cache_total "$c")
    [ -z "$before" ] && { say "  $c: unreachable, skipped"; continue; }
    run docker exec "$c" buildctl prune --keep-storage "$cap" >/dev/null 2>&1
    say "  $c: ${before:-?} -> $(cache_total "$c")  (cap ${cap} MB)"
  done
fi
say

# All-or-nothing by necessity: deleting blobs without bolt.db leaves the index pointing at missing
# files, so a restore FAILS the step instead of missing cleanly. No supported way to expire
# individual rows from outside the runner.
say "--- actions/cache store ---"
if [ ! -d "$ACTCACHE_DIR" ]; then
  say "  $ACTCACHE_DIR not present, skipped"
else
  size_gb=$(du -sBG "$ACTCACHE_DIR" 2>/dev/null | cut -f1 | tr -dc '0-9'); size_gb=${size_gb:-0}
  # Guard with -f: `< "$STATE_FILE"` fails in the SHELL when the file is absent, and that error
  # is printed by bash before tr runs, so a 2>/dev/null on tr cannot suppress it.
  skips=0
  [ -f "$STATE_FILE" ] && skips=$(tr -dc '0-9' < "$STATE_FILE" 2>/dev/null)
  skips=${skips:-0}
  say "  current: ${size_gb} GB   limit: ${ACTCACHE_LIMIT_GB} GB"

  if [ "$size_gb" -le "$ACTCACHE_LIMIT_GB" ]; then
    say "  under limit, nothing to do"
    [ "$skips" -gt 0 ] && run rm -f "$STATE_FILE"
  else
    waited=0
    while ci_busy && [ "$waited" -lt "$WAIT_MAX_SEC" ]; do
      [ "$waited" = 0 ] && say "  over limit, CI busy — waiting up to $((WAIT_MAX_SEC/60)) min for a gap"
      sleep "$POLL_SEC"; waited=$((waited + POLL_SEC))
    done

    if ci_busy; then
      skips=$((skips + 1))
      run bash -c "printf '%s' '$skips' > '$STATE_FILE'"
      say "  STILL BUSY after $((waited/60)) min — skipped. Consecutive skips: $skips"
      [ "$skips" -ge 3 ] && say "  >>> $skips skips in a row; run manually during a quiet period."
    else
      [ "$waited" -gt 0 ] && say "  gap found after $((waited/60)) min"
      say "  resetting store (runner stops briefly)"
      # No -t needed: compose set stop_grace_period 30m, and the runner's shutdown_timeout is 30m.
      run docker stop "$RUNNER" >/dev/null
      # ${VAR:?} aborts if the variable is set-but-empty, which bare "$VAR"/* would not.
      run rm -rf "${ACTCACHE_DIR:?}/cache" "${ACTCACHE_DIR:?}/bolt.db"
      run docker start "$RUNNER" >/dev/null
      run rm -f "$STATE_FILE"
      say "  reset done; first CI run per repo rebuilds its cache."
    fi
  fi
fi
say
say "--- disk ---"; df -h "$HOME" | tail -1
say "Finished."
PRUNE_EOF

chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/.local/bin/prune-ci-caches"
chmod 755 "$RUNNER_HOME/.local/bin/prune-ci-caches"
echo "  $RUNNER_HOME/.local/bin/prune-ci-caches"

echo
echo "=== 2. systemd user units ==="
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 755 "$RUNNER_HOME/.config/systemd/user"

cat > "$RUNNER_HOME/.config/systemd/user/prune-ci-caches.service" <<EOF
[Unit]
Description=Bound Forgejo CI caches (BuildKit + actions/cache)

[Service]
Type=oneshot
Environment=DOCKER_HOST=unix://$SOCK
ExecStart=$RUNNER_HOME/.local/bin/prune-ci-caches
EOF

cat > "$RUNNER_HOME/.config/systemd/user/prune-ci-caches.timer" <<'EOF'
[Unit]
Description=Daily Forgejo CI cache trim

[Timer]
OnCalendar=*-*-* 04:00:00
# Spread the wakeup so it never lands on the same second as anything else.
RandomizedDelaySec=30m
# Catch up after the machine has been off — this is a desktop, not a server.
Persistent=true

[Install]
WantedBy=timers.target
EOF

chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/.config/systemd"
as_runner systemctl --user daemon-reload
as_runner systemctl --user enable --now prune-ci-caches.timer
echo "  timer enabled"

echo
echo "=== 3. repair /usr/local/bin/ci-runner ==="
# The old wrapper used bare `docker` (which resolves to /usr/sbin/docker via PATH) and inline
# env assignments. Neither matches the sudoers rule `(cirunner) NOPASSWD: /usr/bin/docker`, so it
# always prompted. Use the literal path and pass the socket via --host instead of env.
cat > /usr/local/bin/ci-runner <<EOF
#!/usr/bin/env bash
# ci-runner {start|stop|status|logs|...} — anything else is passed to docker compose.
# A manual stop PERSISTS across reboots; restart: unless-stopped will not resurrect it.
set -euo pipefail
cmd="\${1:-status}"
[ \$# -gt 0 ] && shift
[ "\$cmd" = status ] && cmd=ps
exec sudo -u $RUNNER_USER /usr/bin/docker --host unix://$SOCK \\
  compose -f $RUNNER_HOME/.config/forgejo-runner/compose.yaml "\$cmd" "\$@"
EOF
chmod 755 /usr/local/bin/ci-runner
echo "  rewritten to use /usr/bin/docker + --host (matches the NOPASSWD rule)"

echo
echo "=== 4. verify ==="
as_runner systemctl --user list-timers prune-ci-caches.timer --no-pager || true
echo "--- dry run ---"
as_runner env DRY_RUN=1 DOCKER_HOST="unix://$SOCK" "$RUNNER_HOME/.local/bin/prune-ci-caches"
