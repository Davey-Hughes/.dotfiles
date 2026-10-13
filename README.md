# Davey Hughes' .dotfiles

A streamlined, OS-agnostic dotfiles repository powered by [GNU Stow](https://www.gnu.org/software/stow/).

## Requirements

- **GNU Stow**: Must be installed on your system to correctly symlink the dotfiles.
- **Homebrew** (Optional): If installed, the install script will automatically install packages from your OS-specific `Brewfile`.

## Repository Structure

The repository is organized cleanly by symlink target location rather than scattered top-level folders:

- `config/` - Cross-platform configs symlinked into `$XDG_CONFIG_HOME` (defaults to `~/.config/`) — e.g. `fish`, `kitty`, `starship.toml`, `zellij`. Also `config/shell/xdg-env.sh`, the cross-shell XDG environment (see below).
- `home/` - Configs symlinked directly into your home directory `~/` — e.g. `.zshrc`, `.zshenv`, `.bashrc`, `.tmux.conf`, `.inputrc`.
- `os/<platform>/` - Platform-specific trees. A `config/` or `home/` subfolder here is stowed **only on the matching OS**, on top of the common configs. `macos` → `yabai`/`skhd`. Every Arch-derived machine gets `arch` (`paru`, `MangoHud`) plus at most one machine tree beside it: `endeavour` (the desktop — KDE Plasma, `ff-route`, CI runner) or `steamdeck`. Keep `arch` to what holds on *any* Arch box; anything tied to one machine's hardware, screens or workflow belongs in that machine's tree. The two machine trees are disjoint by construction rather than layered — stow treats two packages claiming one path as a conflict, not an override, so a later layer cannot shadow a file an earlier one linked. Layers are selected from `/etc/os-release` (`ID`, falling back to `ID_LIKE`), not `uname`: EndeavourOS runs stock Arch kernels, so `/proc/version` cannot tell it from Arch. Also houses GUI tools, machine scripts, and the Homebrew `Brewfile`s.

## Installation

Simply clone the repository and run the install script:

```bash
git clone https://git.daveynet.xyz/davey/.dotfiles.git ~/.dotfiles
cd ~/.dotfiles
./install.sh
```

The clone path matters: `install.sh` resolves everything from `$HOME/.dotfiles`, so cloning it elsewhere and running it will stow the wrong tree.

**What `install.sh` does:**
1. Validates `stow` is installed.
2. Uses `stow` to symlink the common `config/` (into `$XDG_CONFIG_HOME`) and `home/` (into `~`), then detects your OS and stows any matching `os/<platform>/{config,home}` layer on top. On EndeavourOS, installs KDE snapshots as real, writable files instead of symlinks.
3. Applies essential global Git configurations.
4. Auto-detects your OS (macOS or Linux) and executes `brew bundle` using the appropriate Brewfile inside `os/` (if Homebrew is installed).

## Adding New Configs
To add a new config app down the road, you no longer need to update the install script. Place a cross-platform config in `config/` (or `home/`), or a platform-specific one under `os/<platform>/config` (or `os/<platform>/home`), then rerun `./install.sh`. Stow will detect the new additions and link them on the matching OS.

**One exception.** Stow collapses a directory into a single symlink when the target does not exist — convenient, since files added later then appear without re-stowing. If the app writes back into its own config directory (state, caches, credentials, downloaded plugins), those writes land inside this repo. Add such a directory to `UNFOLD_HOME` or `UNFOLD_CONFIG` at the top of `install.sh`, and to the matching list in `tests/test-install.sh`. Directories only these dotfiles own are better left folded.

## Codex

`config/codex/AGENTS.md` carries personal communication, attribution, shell, and
file-placement preferences. `config/codex/skills/prep-pr/SKILL.md` provides local
branch cleanup; invoke it with `$prep-pr`.

Stow links these into `$XDG_CONFIG_HOME/codex` (normally `~/.config/codex`).
The shell and systemd environment set `CODEX_HOME` to that directory. Codex uses
one home for config, credentials, sessions, and plugins; its root, skills,
themes, and hooks directories stay real so runtime state stays outside the
checkout. On first setup, `install.sh` copies `templates/codex/config.toml` to
`$XDG_CONFIG_HOME/codex/config.toml` as a real, machine-local file. The template
supplies workspace sandboxing, automatic approval review, and the Tokyo Night
Moon theme; project trust, hook trust, and UI state stay in the local copy.
Repeat installs preserve it, so later template edits do not change existing
settings. With a custom `CODEX_HOME`, link the tracked files there and copy the
template only if no config exists.

When migrating an existing install, preserve the entire old directory, back up
its `config.toml`, then copy that config into the new home as a real file. A
compatibility `~/.codex` symlink keeps existing plugin paths and running sessions
resolving. New shells pick up `CODEX_HOME`; GUI launches need a new login.
Review project and hook trust again after changing the config location.

When updating from the old tracked-config layout, back up the live config before
changing revisions, then run `./install.sh` after updating. The installer
replaces the old Stow symlink with a real file and preserves its contents while
the target exists. If the old target has already disappeared, it seeds defaults
instead; restore your backup to recover local preferences and trust state.

`config/codex/themes/tokyonight-moon.tmTheme` matches the Ghostty Tokyo Night Moon
palette for code and diff syntax highlighting. After installation, run `/theme`
in Codex and select **Tokyo Night Moon**. The picker saves the selection in the
machine-local `config.toml`. The themes directory stays real so additional
downloaded themes do not land in this checkout.

`config/codex/hooks.json` registers a destructive-command guard for shell calls.
After installation, start a new Codex chat and use `/hooks` to review and trust
it. The adapter in `config/codex/hooks/` uses the existing
`config/.claude/hooks/rm_guard.py` classifier directly, so the dotfiles checkout
must remain available. It leaves safe commands to normal permissions and blocks
both catastrophic commands and commands the classifier would ask about in
Claude. Git commands pass through, because git bounds its own deletions; the
one exception is a `git clean` on a line that moves its work tree
(`--work-tree`, `--git-dir`, `GIT_DIR`, `GIT_WORK_TREE`, `core.worktree`, or a
`git init`). Codex does not support a hook returning `ask`; this version has no
approval bypass. Inspect and run an intentionally blocked command manually
outside Codex. Run `python3 tests/test-codex-guard.py` to check the adapter.

Classifier failures produce an explicit denial. Codex can still skip untrusted
hooks or continue after a hook infrastructure error or outer timeout, so check
`/hooks` for failures. This guard examines shell command text; it does not
inspect arbitrary scripts or interactive input sent to an existing process.

Codex uses Vim composer input by default. `/vim` toggles it in an existing
session. Its terminal title shows the chat name, which tmux uses for automatic
window names; manually renamed windows keep their names. Activity icons and
prefix-`u` cover both Claude and Codex panes.

Codex runs lifecycle hooks in its shared app-server daemon, with the
environment that daemon started in, so `TMUX_PANE` in a hook names the pane
Codex was first launched from, not the one showing the conversation. The title
therefore ends in the thread id (`tui.terminal_title = ["thread", "thread-id"]`
in the template), the adapter `config/codex/hooks/tmux-status.sh` finds the pane
by it, and tmux drops it from the window name. A `config.toml` seeded before
this keeps its old title: add `thread-id` after `thread` there, or with
`/title`, and restart Codex. A session without it gets no activity icon and is
not registered for resurrect.

The shared resurrect save hook also handles Codex sessions. Lifecycle hooks
record the current conversation and live process identity under
`$XDG_STATE_HOME/codex/tmux-sessions`. On Linux, each manual or continuum save
checks the process start time, a unique Codex process in the pane, and an
existing transcript before saving `codex resume <id>`. Start or resume Codex,
or submit a prompt, to register an existing pane. Uncertain matches are left
unchanged. The dotfiles checkout and trusted lifecycle hooks are required.

## OpenClaw

OpenClaw defaults to one `~/.openclaw` for everything. Here it is split along
XDG lines by `OPENCLAW_CONFIG_PATH` and `OPENCLAW_STATE_DIR` (set in all three
XDG env files):

- `$XDG_CONFIG_HOME/openclaw/openclaw.json` — the config file. Machine-local
  and **untracked**: it holds the gateway token, auth profile ids and wizard
  state, and OpenClaw replaces it by atomic rename, so it cannot be a stow
  symlink (OpenClaw also refuses a symlinked parent, hence `openclaw` in
  `UNFOLD_CONFIG`).
- `$XDG_DATA_HOME/openclaw` — state database, sessions, credentials, logs, and
  the agent workspace (a git repo of its own).

The tracked part lives in `config/openclaw/` and is stowed in beside the real
config file. `config/openclaw/skills.json5` is pulled in by
`skills: { $include: "./skills.json5" }` and points OpenClaw at the personal
skills in `config/openclaw/skills/` (`prep-pr`, ported from Codex, and
`i-have-adhd`, ported from Claude). Because the fragment resolves into this
checkout, `OPENCLAW_INCLUDE_ROOTS` admits it and OpenClaw treats it as
read-only: `openclaw config set skills.*` fails closed — edit the fragment
instead. Add further fragments the same way, for sections OpenClaw does not
write on its own.

The gateway's user unit is generated by `openclaw gateway install`, so its
environment comes from the drop-in
`os/arch/config/systemd/user/openclaw-gateway.service.d/xdg.conf`, which a
regenerated unit cannot lose.

With the config and state in non-default places, OpenClaw treats that unit as
*diagnostic only*: systemd still starts it at login and restarts it on a
crash, but OpenClaw's own tooling no longer stops, restarts or rewrites it.
Two places that shows:

- **Upgrades.** OpenClaw comes from the Arch package, so upgrade it with
  `paru` (`openclaw update` refuses a pacman-owned install). pacman never
  restarts user services, so restart the gateway afterwards:
  `systemctl --user restart openclaw-gateway`.
- **Doctor repairs.** `openclaw doctor --fix` no longer stops and restarts the
  gateway around its repairs. Do it by hand:

  ```bash
  systemctl --user stop openclaw-gateway
  openclaw doctor --fix
  systemctl --user start openclaw-gateway
  ```

  Run that from a normal terminal: stopping the gateway ends any OpenClaw chat.

Migrating an existing install: run `scripts/openclaw-xdg-migrate.sh` (dry-run),
then `scripts/openclaw-xdg-migrate.sh --apply` **from a normal terminal** — it
stops the gateway, moves `~/.openclaw` to the state dir and its config to the
config dir, repoints the absolute paths the wizard wrote, adds the
`$include`, and starts the gateway again. Follow with `openclaw doctor`. On a
fresh machine run `openclaw onboard` first (with the env loaded it writes to the
XDG paths), then the script to add the `$include`.

### tmux

OpenClaw TUI panes get the same tmux treatment as Claude and Codex: the
activity glyph in the tab, prefix-`u`, window names, and resurrect. The agent
runs in the Gateway, which need not be this machine, so nothing in the pane
sees a turn start or end. A small Gateway client beside tmux does it instead:
`config/openclaw/tmux-status/bridge.js`, run by the `openclaw-tmux-status` user
unit. It subscribes read-only to the Gateway's session events. On each turn,
tool call, `ask_user` or `secrets` prompt, and subagent start or end, it finds
the panes whose TUI footer shows that session (`tmux-session.sh`, the one
footer parser) and runs the shared `config/.claude/hooks/tmux-status.sh` for
each, with `TMUX_PANE` set. The session label in the footer names the window,
the way a Claude `/rename` does. A pane that switches session (`/session`,
`/new`, Ctrl+P) is reset within a few seconds.

It connects where the CLI would: `gateway.remote.url` and its token when
`gateway.mode` is `remote` in `openclaw.json`, else the local Gateway.
`OPENCLAW_TMUX_STATUS_URL`, `OPENCLAW_GATEWAY_TOKEN` and
`OPENCLAW_GATEWAY_PASSWORD` override. Its client package is pinned in
`package-lock.json`; the launcher installs it into
`$XDG_DATA_HOME/openclaw-tmux-status`, not this checkout, and again whenever
the lockfile changes. Bump the pin with the Gateway. The bridge has its own
device identity under `$XDG_STATE_HOME/openclaw-tmux-status`.

Enable it once per machine:

```bash
systemctl --user enable --now openclaw-tmux-status
journalctl --user -u openclaw-tmux-status
```

A remote Gateway pairs it like any other device: the log prints the request,
approve it on the Gateway host with `openclaw devices approve <requestId>`, and
the unit retries every 30 seconds.

Reload tmux (prefix-`R`) to pick up the icon, window-name, and resurrect changes
in `.tmux.conf`. After editing the bridge, `systemctl --user restart
openclaw-tmux-status`. Run `python3 tests/test-openclaw-tmux.py` to check it
against a fake tmux.

Limits: an exec approval shows as the turn still working rather than as a
prompt. The footer format is the contract with the TUI and the session-event
shapes are the contract with the Gateway. If a release changes either, painting
fails closed: panes show no state and restore as shells.

Restore saves each placeable pane as `openclaw tui --session <key>`. A bare
`openclaw` would reopen the TUI's last selected session, so
`@resurrect-processes` matches `"openclaw tui"` and an unplaced pane comes back
as a shell.

Not ported from Claude: the `rm` guard (OpenClaw has its own exec approvals) and
the TUI settings. Claude's `CLAUDE.md` rules live in the agent workspace's `USER.md`.

## KDE Plasma (desktop only)

KDE Plasma settings (theme, shortcuts, panels, Dolphin/Konsole/Kate) are curated
snapshots under `os/endeavour/kde/config/`, outside the Stow tree. KDE writes to
real files under `$XDG_CONFIG_HOME` (normally `~/.config`), so everyday desktop
activity leaves this repo alone. Python 3 is required on the desktop.

On EndeavourOS, `install.sh` seeds missing configs and converts legacy KDE
Stow links into real files, preserving their full contents. Folded Kate and
`kdedefaults` directory links are copied with their extra local files intact.
Existing real configs are preserved on repeat installs. Stow ignores legacy
KDE sources, so an old file left in its source tree cannot relink the desktop.
The Steam Deck keeps stock SteamOS Plasma.

After changing preferences in System Settings, preview and capture them:

```bash
python3 scripts/kde-config.py capture          # preview only
python3 scripts/kde-config.py capture --write  # save the displayed changes
git diff -- os/endeavour/kde/config
```

Capture reads only files already selected by the snapshot tree. It omits
desktop UUIDs and output-specific tiling groups, wallpaper paths, color-scheme
hashes, file-view timestamps, applet tabs/dialog geometry, activity/screen
bindings, notification discovery state, and similar known runtime fields.
The exclusions are listed in `scripts/kde-config.py`; review the diff before
committing, since new KDE versions or plugins can add fields. To manage a new
config, add a snapshot file first, then capture it.

To apply tracked preferences to this desktop:

```bash
python3 scripts/kde-config.py apply --dry-run
python3 scripts/kde-config.py apply
```

Apply merges snapshot keys into live files, preserving unmentioned local keys,
groups, and wallpaper paths. It does not delete extra local applets or settings.
Snapshots retain panel/applet IDs and device-specific mouse preferences; they
remain specific to this desktop and do not remap layouts onto different
hardware. Output-specific KWin tiling remains local. Applications may need a
restart or a new login to read applied settings; apply while the affected apps
are closed or from a separate session to avoid their in-memory settings
overwriting the files. The command does not restart the desktop.

For a manual migration, run `python3 scripts/kde-config.py install --dry-run`,
then `python3 scripts/kde-config.py install`. Migration and apply save changed
live contents under `$XDG_STATE_HOME/dotfiles/kde-backups` (normally
`~/.local/state/dotfiles/kde-backups`). Migrate before deleting old live Stow
targets; a dangling legacy link can only be seeded from the curated snapshot,
which cannot recover omitted runtime state. Foreign symlinks are refused.
The old wallpaper Git filter is retired; its state sidecar is left in place
for recovery, but is no longer used.

**Dependency:** `kwinrc` enables the [`krohnkite`](https://github.com/anametologin/krohnkite)
tiling script. Install it from the AUR (e.g. `paru -S kwin-scripts-krohnkite`) for
tiling to work; the config installs fine without it, tiling just stays inactive.

Hardware/state files (monitor layout, file-index state, KDE Connect pairing keys,
activity stats) are intentionally **not** tracked.

## Shell environment & XDG
`config/shell/xdg-env.sh` (POSIX, for `bash`/`zsh`), `config/fish/conf.d/xdg.fish` (fish), and `config/environment.d/xdg.conf` (systemd user session, Linux) define the XDG base directories and redirect many tools' cache/data/config out of `$HOME` into XDG locations. The three files are kept in sync — `tests/test-xdg-sync.sh` enforces that, and lists the one documented exemption.

The env vars only redirect *future* writes. On a fresh machine, run `scripts/xdg-migrate.sh` (dry-run) and then `scripts/xdg-migrate.sh --apply` to move any pre-existing tool dirs (`~/.cargo`, `~/.npm`, …) into their XDG homes. It is idempotent and skips anything already migrated. OpenClaw has its own script, `scripts/openclaw-xdg-migrate.sh` (see [OpenClaw](#openclaw)), because its gateway has to be stopped for the move.

## Tests

```bash
./tests/run-all.sh        # everything, ~30s
./tests/test-install.sh   # or any single suite, on its own
```

The same set `.forgejo/workflows/ci.yml` runs on every push. There is nothing to
build here, so the checks cover the three ways a dotfiles repo actually breaks:

| Check | What it catches |
| --- | --- |
| `config/.claude/hooks/test_rm_guard.py` | The `rm` guard hook approving something it should have asked about. Runs on every push because its regressions are measured in deleted files. |
| `tests/test-tracked-files.sh` | Anything tracked that must not be. `config/.claude/` is a live tool directory — sessions, transcripts and OAuth state sit untracked inside a tracked parent, held back by a `.gitignore` allowlist. Also verifies KDE snapshots omit local wallpaper paths. |
| `tests/test-kde-config.py` | Capture leaking runtime state, apply losing local settings, or migration leaving writable configs linked into the repo. Tests files, folded directories, missing legacy targets, repeat installs, and the EndeavourOS installer hook. |
| `tests/test-syntax.sh` | A file that will not parse in the shell or program that reads it — `bash -n`, `zsh -n`, `fish --no-execute`, JSON/TOML/YAML, plus `shellcheck` at warning level on the scripts. |
| `tests/test-xdg-sync.sh` | The three XDG env files drifting apart, which silently gives one shell a different environment than the others. |
| `tests/test-docs.sh` | A path this README names that no longer exists. |
| `tests/test-install.sh` | `install.sh` failing on a machine that is not already set up. Runs it twice against a throwaway `$HOME` — the conflict/backup path, idempotence, and stow's directory folding in both directions: every shared directory comes out real, every submodule still resolves into the checkout. Its expected list mirrors `UNFOLD_HOME`/`UNFOLD_CONFIG` rather than reading them, so dropping an entry fails the build instead of silently dropping its own assertion. |

A checker that is not installed locally (`fish`, `shellcheck`, PyYAML) prints
`skip`; in CI the same absence is a hard failure, so nothing goes unchecked there.

## ZSH
The custom ZSH theme included is originally based on the `bira`, `gnzh`, `phil!`'s, and `nanotech` themes.
