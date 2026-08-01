# ci-runner — Forgejo Actions runner on this desktop

A second CI runner that shares work with the one on DaveyNet, so Rust builds use this box's 32
threads instead of the server's 8. It polls `https://forge.daveynet.xyz/` outbound — no inbound
ports, no VPN, works behind NAT. When this machine is off, jobs simply go to DaveyNet; nothing
queues or fails.

Live since 2026-08-01.

## Files

| Path | |
|---|---|
| `../../home/.local/bin/ci-watch` | day-to-day inspection command |
| `setup-cirunner.sh` | full provisioning, idempotent |
| `install-prune-arch.sh` | cache bounding + timer, idempotent |

Runtime state lives under the `cirunner` user, not here:

```
~cirunner/.config/forgejo-runner/   compose.yaml + config.yml
~cirunner/.local/share/…/           /data, holds .runner   ← registration, do not delete
~cirunner/.cache/forgejo-runner/    action repos + cache blobs, safe to wipe
```

## Why a separate user

The obvious setup mounts the root-owned `/var/run/docker.sock` into every job container, which
hands any workflow step full root on this machine. That matters even for repos you trust: `npm ci`
runs arbitrary postinstall scripts from the whole dependency tree, so the binding constraint is
your transitive deps, not your own code.

Instead, CI runs as `cirunner` against **that user's own rootless dockerd**:

- locked password, no sudo, no SSH, home `drwx------`
- socket at `/run/user/1001/docker.sock`, in a `0700` dir — `davey` cannot reach it either
- container-root maps into cirunner's subuid range, not host root
- `/home/davey` is `drwx------`, so a compromised job cannot read it

Residual risk: `cirunner` still has LAN access and can burn CPU/disk. A VM is the only real
boundary; this is a large reduction, not a seal.

## Routing

Both runners carry the `docker` label — which is what every workflow already uses — so ordinary
jobs go to whichever is free. That is the offload, and it needed no repo changes.

| Label | Goes to |
|---|---|
| `docker` | either runner |
| `arch` | this box only |
| `unraid` | DaveyNet only |

Pinning ws-sim's build+push job to `unraid` is still worth doing — its ~40 GB BuildKit cache and
the What's-Up-Docker redeploy loop both live there — but it needs a workflow edit, which triggers
a CI run and a service redeploy.

## Commands

```sh
ci-watch            # runner, live jobs (with repo names), builders, caches, recent log
ci-watch -f         # follow the log
ci-watch top        # docker stats for job containers only
ci-watch history    # recent tasks handled

ci-runner status|stop|start|logs    # /usr/local/bin/ci-runner, written by setup-cirunner.sh
```

A manual `ci-runner stop` **persists across reboots** — `restart: unless-stopped` will not
resurrect a container stopped on purpose.

## Cache bounding

Two caches grow independently and neither is bounded by default:

- **BuildKit** — one volume per buildx builder. buildkitd self-GCs, but its default ceiling is
  `Reserved 10GB, Free 382GB, Maximum 100GB` *per builder*.
- **actions/cache** — `bolt.db` (key → blob id) plus `cache/<shard>/<id>` blobs. The runner GCs by
  **age** (7d unused / 30d used), never by **size**. On DaveyNet this reached 30 GB in two weeks.

`install-prune-arch.sh` installs a systemd **user** timer (daily 04:00, `Persistent=true` so a
sleeping desktop catches up) that caps each builder and resets the actions/cache store above
40 GB. It waits up to 15 min for a gap in CI rather than skipping, and counts consecutive misses.

The actions/cache reset is all-or-nothing by necessity: deleting blobs without `bolt.db` leaves the
index pointing at missing files, so a restore **fails the step** instead of missing cleanly.

## Four things that will bite on a rebuild

1. **Mount the docker socket at the identical path on both sides** (`$SOCK:$SOCK`, never
   `$SOCK:/var/run/docker.sock`). `container.docker_host` does double duty — it is the socket the
   runner dials from *inside* its container **and** the *host* path mounted into each job
   container. Getting it wrong fails with `cannot ping the docker daemon`.

2. **`--user 0:0` on the runner container is correct and is not a privilege grant.** Under rootless
   docker, container root maps to host uid 1001. The image's default uid 1000 maps into the subuid
   range (~166535) and cannot write the `drwx------` dirs owned by 1001 — that gives
   `open .runner: permission denied`.

3. **Arch has no `dockerd-rootless-setuptool.sh`.** `docker-rootless-extras` ships
   `/usr/lib/systemd/user/docker.{socket,service}`; use `systemctl --user enable --now docker.socket`
   plus `loginctl enable-linger cirunner`.

4. **`shutdown_timeout` defaults to `0s`, not the `3h` that `generate-config` prints** — that value
   is the template's suggestion, not the runtime default. Unset means SIGTERM cancels running jobs
   instantly. Two settings are needed, and the docker one must be ≥ the runner one:
   `runner.shutdown_timeout` in `config.yml`, and `stop_grace_period` on the compose service
   (→ container `StopTimeout`; unset = 10s, which cuts the drain off regardless).

## sudoers

`ci-watch` and `ci-runner` rely on:

```
davey ALL=(cirunner) NOPASSWD: /usr/bin/docker
```

in `/etc/sudoers.d/ci-runner`. This grants no new privilege — davey already has full sudo — it only
removes the prompt. It matches the **literal** path, so both scripts must call `/usr/bin/docker`
(bare `docker` resolves to `/usr/sbin/docker` on this box's PATH) and must avoid inline
`VAR=value` assignments, which break command matching independently. Both use `--host` instead of
`DOCKER_HOST` for that reason.

## Rebuild from scratch

```sh
sudo ~/.config/ci-runner/setup-cirunner.sh      # needs a registration token first
sudo ~/.config/ci-runner/install-prune-arch.sh
```

Get the token on the server with
`docker exec -u 1000 forgejo forgejo actions generate-runner-token`, and write it to
`~/.config/forgejo-runner/.regtoken` (mode 600) — `setup-cirunner.sh` reads and shreds it.
