#!/usr/bin/env bash
#
# Install a Forgejo Actions runner under a dedicated unprivileged user with its own
# ROOTLESS docker daemon.
#
# WHY: the default setup mounts the host's root-owned /var/run/docker.sock into every job
# container, which hands any workflow step full root on this machine. `npm ci` alone runs
# arbitrary postinstall scripts from the whole dependency tree, so "I trust my own repos" is
# not the binding constraint. Here, CI's blast radius is the `cirunner` user:
#
#   - cannot read /home/davey (verified drwx------)
#   - no sudo, locked password, no SSH
#   - the docker daemon it talks to is rootless, so container-root maps to cirunner's
#     subuid range, NOT host root
#
# Residual risk, stated plainly: cirunner can still use the network (your LAN), burn CPU/disk,
# and persist as itself. This is a large reduction, not a vacuum seal. A VM is the only real
# boundary.
#
# Idempotent — safe to re-run.

set -euo pipefail

RUNNER_USER=cirunner
RUNNER_HOME=/home/$RUNNER_USER
FORGE_URL=https://forge.daveynet.xyz/
RUNNER_NAME=arch-endeavor
TOKEN_FILE=/home/davey/.config/forgejo-runner/.regtoken
SUBID_START=200000
SUBID_COUNT=65536

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

say() { printf '\n=== %s ===\n' "$*"; }

# ---------------------------------------------------------------- 1. user ----
say "1. dedicated user"
if id "$RUNNER_USER" &>/dev/null; then
  echo "  $RUNNER_USER exists"
else
  useradd --create-home --home-dir "$RUNNER_HOME" --shell /bin/bash "$RUNNER_USER"
  passwd --lock "$RUNNER_USER" >/dev/null   # no password login; no sudo group either
  echo "  created $RUNNER_USER"
fi
chmod 700 "$RUNNER_HOME"
RUID=$(id -u "$RUNNER_USER")
echo "  uid=$RUID home=$RUNNER_HOME"

# subuid/subgid ranges are what make rootless mapping work. davey holds 100000-165535.
if ! grep -q "^$RUNNER_USER:" /etc/subuid; then
  usermod --add-subuids "$SUBID_START-$((SUBID_START + SUBID_COUNT - 1))" \
          --add-subgids "$SUBID_START-$((SUBID_START + SUBID_COUNT - 1))" "$RUNNER_USER"
  echo "  allocated subuid/subgid $SUBID_START+"
else
  echo "  subuid/subgid already allocated"
fi

# ------------------------------------------------------------ 2. packages ----
say "2. rootless docker tooling"
if pacman -Qq docker-rootless-extras &>/dev/null; then
  echo "  docker-rootless-extras already installed"
else
  pacman -S --needed --noconfirm docker-rootless-extras
fi

# ------------------------------------------------------- 3. user session -----
say "3. systemd lingering (user daemon runs without login)"
loginctl enable-linger "$RUNNER_USER"
for _ in $(seq 30); do
  [ -d "/run/user/$RUID" ] && break
  sleep 1
done
[ -d "/run/user/$RUID" ] || { echo "  /run/user/$RUID never appeared"; exit 1; }
echo "  /run/user/$RUID ready"

# Helper: run a command as the runner user inside its systemd user session.
as_runner() {
  sudo -u "$RUNNER_USER" \
    XDG_RUNTIME_DIR="/run/user/$RUID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$RUID/bus" \
    PATH=/usr/bin:/bin \
    "$@"
}

# --------------------------------------------------- 4. rootless daemon ------
say "4. rootless dockerd"
# Arch does NOT ship dockerd-rootless-setuptool.sh. The package provides user units instead:
#   /usr/lib/systemd/user/docker.socket   ListenStream=%t/docker.sock  -> /run/user/<uid>/docker.sock
#   /usr/lib/systemd/user/docker.service  ExecStart=/usr/bin/dockerd-rootless.sh
# The post-install message documents enabling the SOCKET only, so try that first and prove the
# daemon actually answers before moving on — falling back to the service if it does not.
SOCK="/run/user/$RUID/docker.sock"

daemon_ok() { as_runner env DOCKER_HOST="unix://$SOCK" docker version >/dev/null 2>&1; }

as_runner systemctl --user enable --now docker.socket
for _ in $(seq 10); do daemon_ok && break; sleep 1; done

if ! daemon_ok; then
  echo "  socket activation alone did not yield a working daemon; enabling docker.service"
  as_runner systemctl --user enable --now docker.service
  for _ in $(seq 30); do daemon_ok && break; sleep 1; done
fi

daemon_ok || { echo "  rootless daemon not responding on $SOCK"; \
  as_runner systemctl --user status docker.service --no-pager -l | tail -20; exit 1; }

[ -S "$SOCK" ] || { echo "  expected socket $SOCK missing"; exit 1; }
echo "  rootless daemon up: $(as_runner env DOCKER_HOST="unix://$SOCK" docker version --format '{{.Server.Version}}')"
echo "  socket: $SOCK (owned by $RUNNER_USER, NOT root)"

# ------------------------------------------------------------- 5. config -----
say "5. runner config (XDG under $RUNNER_HOME)"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 700 \
  "$RUNNER_HOME/.config/forgejo-runner" \
  "$RUNNER_HOME/.local/share/forgejo-runner" \
  "$RUNNER_HOME/.cache/forgejo-runner"

cat > "$RUNNER_HOME/.config/forgejo-runner/config.yml" <<EOF
log:
  level: info

runner:
  # 2 concurrent jobs x 12 CPUs = 24 of this box's 32 threads, leaving 8 for the desktop.
  capacity: 2
  # 'docker' is what every existing workflow uses, so this shares ordinary work with
  # DaveyNet's runner immediately. 'arch' pins a job here explicitly.
  labels: ["docker", "arch"]
  # SIGTERM drains running jobs. Must be <= stop_grace_period in compose.yaml.
  shutdown_timeout: 30m

cache:
  enabled: true
  dir: /data/.cache/actcache

container:
  network: forgejo_runner_net
  # THE SECURITY-CRITICAL LINE. Explicitly the ROOTLESS socket, never /var/run/docker.sock.
  # "automount" would search and could find the root daemon's socket instead. A job that
  # escapes through this one gets $RUNNER_USER, not root.
  docker_host: "unix://$SOCK"
  options: "--cpus 12"
EOF

cat > "$RUNNER_HOME/.config/forgejo-runner/compose.yaml" <<EOF
# Runs on $RUNNER_USER's ROOTLESS daemon. Every docker command for this stack needs
# DOCKER_HOST=unix://$SOCK — see the on/off wrapper at /usr/local/bin/ci-runner.
name: forgejo-runner

services:
  runner:
    image: code.forgejo.org/forgejo/runner:12
    container_name: forgejo-runner
    restart: unless-stopped
    # Container root, which under ROOTLESS docker maps to host uid $RUID ($RUNNER_USER) — not
    # to real root. The image defaults to uid 1000, which rootless maps into the subuid range
    # (~166535) and which therefore cannot write these drwx------ dirs owned by $RUID.
    # On a rootful daemon this line would be a privilege grant; here it is the opposite.
    user: "0:0"
    volumes:
      - $RUNNER_HOME/.local/share/forgejo-runner:/data
      - $RUNNER_HOME/.cache/forgejo-runner:/data/.cache
      - $RUNNER_HOME/.config/forgejo-runner/config.yml:/data/config.yml:ro
      # SAME PATH BOTH SIDES — deliberate, do not "tidy" this to /var/run/docker.sock.
      # container.docker_host does double duty: it is the socket this runner dials from INSIDE
      # its container, and the HOST path mounted into each job container. Mounting it anywhere
      # else makes the config path nonexistent in here and the runner exits with
      # "cannot ping the docker daemon".
      - $SOCK:$SOCK
    stop_grace_period: 30m
    command: ["/bin/forgejo-runner", "daemon", "--config", "/data/config.yml"]

networks:
  default:
    name: forgejo_runner_net
EOF

chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME/.config/forgejo-runner"
echo "  config.yml + compose.yaml written"

# ----------------------------------------------------------- 6. register -----
say "6. register with Forgejo"
if [ -f "$RUNNER_HOME/.local/share/forgejo-runner/.runner" ]; then
  echo "  already registered, skipping"
else
  [ -f "$TOKEN_FILE" ] || { echo "  token file $TOKEN_FILE missing"; exit 1; }
  TOKEN=$(cat "$TOKEN_FILE")
  # --user 0:0 for the same reason as compose.yaml: under rootless docker, container root IS
  # host uid $RUID. The image's default uid 1000 maps into the subuid range and cannot write
  # /data, which is what produced "open .runner: permission denied".
  as_runner env DOCKER_HOST="unix://$SOCK" docker run --rm --user 0:0 \
    -v "$RUNNER_HOME/.local/share/forgejo-runner:/data" \
    -v "$RUNNER_HOME/.cache/forgejo-runner:/data/.cache" \
    code.forgejo.org/forgejo/runner:12 \
    /bin/forgejo-runner register --no-interactive \
      --instance "$FORGE_URL" --token "$TOKEN" \
      --name "$RUNNER_NAME" --labels docker,arch
  unset TOKEN
  shred -u "$TOKEN_FILE" 2>/dev/null || rm -f "$TOKEN_FILE"
  echo "  registered; token file shredded"
fi

# -------------------------------------------------------------- 7. start -----
say "7. start"
as_runner env DOCKER_HOST="unix://$SOCK" \
  docker compose -f "$RUNNER_HOME/.config/forgejo-runner/compose.yaml" up -d

# ------------------------------------------------------------ 8. wrapper -----
say "8. on/off wrapper"
cat > /usr/local/bin/ci-runner <<EOF
#!/usr/bin/env bash
# ci-runner {start|stop|status|logs|...} — manage the CI runner without remembering the
# rootless DOCKER_HOST dance. Anything not listed is passed straight to docker compose.
#
# A manual stop PERSISTS across reboots: restart: unless-stopped will not resurrect a
# container that was stopped on purpose.
set -euo pipefail

cmd="\${1:-status}"
[ \$# -gt 0 ] && shift
[ "\$cmd" = status ] && cmd=ps

exec sudo -u $RUNNER_USER \\
  XDG_RUNTIME_DIR=/run/user/$RUID \\
  DOCKER_HOST=unix://$SOCK \\
  docker compose -f $RUNNER_HOME/.config/forgejo-runner/compose.yaml "\$cmd" "\$@"
EOF
chmod 755 /usr/local/bin/ci-runner
echo "  installed /usr/local/bin/ci-runner"

say "done"
echo "  ci-runner status | stop | start | logs"
echo "  CI blast radius is now the '$RUNNER_USER' user, not root."
