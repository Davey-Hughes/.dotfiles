# ff-route

Sends each opened link to the correct Firefox profile instead of letting Firefox
guess.

## Why this exists

The stock handler is `firefox %u` with no profile named. Firefox then resolves a
profile from `~/.config/mozilla/firefox/installs.ini`:

```
[46F492E0ACFF84D4]
Default=5f9knhb5.dev-edition-default
```

That is a single global value rewritten when you switch profiles — it has nothing
to do with which window you were looking at. Hence links landing in whichever
profile was touched last.

`firefox -P <name> <url>` is deterministic instead. Each profile registers its own
D-Bus name (`org.mozilla.firefox_developer_edition.<base64 profile path>`), so a
profile-targeted launch remotes into that exact instance, spawns no new process,
and does not modify `installs.ini`.

## Files

| Path | Role |
Tracked in the `os/endeavour` layer of `~/.dotfiles` and stowed to these targets:

| Repo path | Stowed to | Role |
| --- | --- | --- |
| `os/endeavour/config/ff-route/rules.conf` | `~/.config/ff-route/` | the rules — edit this |
| `os/endeavour/config/ff-route/install-handler.sh` | `~/.config/ff-route/` | one-off per-machine registration |
| `os/endeavour/home/.local/bin/ff-route` | `~/.local/bin/` | the router |
| `os/endeavour/home/.local/share/applications/ff-route.desktop` | `~/.local/share/applications/` | default http/https handler |
| `os/endeavour/home/.local/share/applications/firefox-work.desktop` | ″ | direct work launcher (pinnable, "Open With") |
| `os/endeavour/home/.local/share/applications/firefox-personal.desktop` | ″ | direct personal launcher |
| `os/endeavour/home/.local/share/applications/slack.desktop` | ″ | override of the system entry, adds `FF_PROFILE=beaver` |

## Setting this up on a new machine

```sh
./install.sh                             # stows the symlinks
~/.config/ff-route/install-handler.sh    # registers the http/https association
```

The second step is separate because the association lives in
`~/.config/mimeapps.list`, which is machine state and not tracked.

Two things in the repo assume this machine:

- The `.desktop` files hardcode `/home/davey/...` in `Exec=`. Desktop entries do
  not expand `~` or `$HOME`, and `~/.local/bin` is absent from the systemd user
  manager's `PATH` — which is what actually launches URL handlers — so a bare
  command name would not resolve either. Edit the paths if `$HOME` ever differs.
- `slack.desktop` is a copy of `/usr/share/applications/slack.desktop` with an
  `env FF_PROFILE=beaver` prefix. If the Slack package changes its flags, re-copy
  it and re-apply the prefix.

## Decision order

First match wins:

1. **`$FF_PROFILE`** — set on an app's `.desktop` Exec line. Slack uses this.
2. **`PERSONAL_URL_PATTERNS`** — escape hatch, empty by default.
3. **`WORK_URL_PATTERNS`** — hard work-only URLs, win regardless of source.
4. **tmux session** — the pane's session name against `WORK_TMUX_SESSIONS`.
5. **`FALLBACK`** — personal.

## How source detection works, and why it is done this way

Inferring the calling app by walking `/proc` **does not work here**. KDE launches
URL handlers as transient systemd units, so the handler's parent is
`systemd --user` and the caller is already gone:

```
0::/user.slice/.../app.slice/app-ff\x2droute@<id>.service
```

Environment variables *are* inherited intact, so the source is **declared**
rather than inferred:

- **GUI apps** — copy the `.desktop` file into `~/.local/share/applications/` and
  prefix `Exec=` with `env FF_PROFILE=beaver`. Restart the app for it to take
  effect.
- **Terminals** — Ghostty runs single-instance, so a per-window env var is not
  possible. `$TMUX_PANE` survives to the handler though, and resolves back to a
  session name via `tmux display-message -p -t "$TMUX_PANE" '#{session_name}'`.
  That is what distinguishes work from personal panes.

## Adding rules

Edit `rules.conf`, then check without opening anything:

```sh
ff-route --explain https://github.com/beaver-home/infra
ff-route --test        # built-in routing tests
ff-route --log 20      # the last 20 real routing decisions
```

`--explain` prints the matched rule, the detected tmux session, and the exact
command it would run. Run it from the pane you are curious about — the tmux
detection is only meaningful in context.

## Adding a new work tmux session

Append the name to `WORK_TMUX_SESSIONS` in `rules.conf`. No restart needed; the
config is read on every invocation.
