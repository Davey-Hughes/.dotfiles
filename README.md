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

OpenClaw runs in remote mode here: the Gateway is on another host, and this
machine's CLI, TUI, tmux bridge and node host connect to it.

Config and state are split along XDG lines by `OPENCLAW_CONFIG_PATH` and
`OPENCLAW_STATE_DIR` (set in all three XDG env files). `openclaw.json` is
machine-local and untracked: OpenClaw replaces it by atomic rename and refuses
a symlinked parent directory, hence `openclaw` in `UNFOLD_CONFIG`. The tracked
part, `config/openclaw/`, is stowed in beside it. `skills.json5` is pulled in by
`skills: { $include: "./skills.json5" }` and points OpenClaw at the personal
skills in `config/openclaw/skills/`. `OPENCLAW_INCLUDE_ROOTS` admits the
fragment, and OpenClaw treats it as read-only: edit the file rather than
running `openclaw config set skills.*`.

Upgrade with `paru` (`openclaw update` refuses a pacman-owned install), and keep
the CLI at the Gateway's version. When the AUR lags behind, bump the package by
hand; paru takes over again at the next AUR release:

```bash
cd (mktemp -d)
paru -G openclaw; and cd openclaw
sed -i 's/^_pkgver=.*/_pkgver=<version>/' PKGBUILD
updpkgsums
makepkg -si
```

`scripts/openclaw-xdg-migrate.sh` moves an existing `~/.openclaw` to the XDG
paths and adds the `$include`. It is a dry run unless given `--apply`.

### tmux

OpenClaw TUI panes get the same tmux treatment as Claude and Codex: the
activity glyph in the tab, prefix-`u`, window names, and resurrect. The agent
runs in the Gateway, so nothing in the pane sees a turn start or end; instead
`config/openclaw/tmux-status/bridge.js`, a read-only Gateway client, follows the
session events and paints the panes whose TUI footer shows that session. It
connects where the CLI does. Its client package is pinned in
`package-lock.json` and installed into `$XDG_DATA_HOME/openclaw-tmux-status`,
not this checkout; bump the pin with the Gateway. Restore saves each pane as
`openclaw tui --session <key>`. `python3 tests/test-openclaw-tmux.py` checks the
bridge against a fake tmux.

### User units

`openclaw-tmux-status` runs the bridge and `openclaw-node` runs a node host.
Both are machine-local: create them in `os/arch/config/systemd/user/`, where
`.gitignore` keeps them out of git and `install.sh` still links them. The node's
unit is written by hand because `openclaw node install` refuses to manage
services while the state dir and config path are the XDG ones.

```ini
# openclaw-tmux-status.service
[Service]
ExecStart=%h/.config/openclaw/tmux-status/openclaw-tmux-status
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

```ini
# openclaw-node.service
[Service]
ExecStart=/usr/bin/openclaw node run --host <gateway-host> --port <port> --display-name %H
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

Enable them with `systemctl --user enable --now <unit>`. Each pairs with the
Gateway like any other device, and retries until it is approved there.

## OpenCode

`templates/opencode/cli.json` holds the terminal settings (theme, input,
sessions, tabs). On first setup, `install.sh` copies it to
`$XDG_CONFIG_HOME/opencode/cli.json` as a real, machine-local file: the settings
screen (`Ctrl+P`, **Open settings**) saves by writing a new file over the old
one, which would replace a Stow link. Repeat installs keep the local copy, so
copy changes worth keeping back into the template by hand. That directory
stays real (listed in `UNFOLD_CONFIG`) because the background service also
writes `service.json`, its local password, there. `.gitignore` admits
`opencode.json(c)` and the agents, commands, skills and plugins directories for
global server settings; add them under `config/opencode/` and rerun
`./install.sh`.

`config/opencode/opencode.jsonc` is the global server config, linked in by
Stow. It loads the [Superpowers](https://github.com/obra/superpowers) skills as
a git-backed plugin, the same set Claude Code gets from its marketplace.
OpenCode installs the package into its cache on the next config reload. If a
newer Superpowers commit does not appear, clear the cache under
`$XDG_CACHE_HOME/opencode` or pin a tag (`...superpowers.git#v7.0.1`).

Sessions, logs and caches live under `$XDG_DATA_HOME`,
`$XDG_STATE_HOME` and `$XDG_CACHE_HOME`, outside this repo.

### tmux

OpenCode panes get the same tmux treatment as Claude and Codex: the activity
glyph in the tab, prefix-`u`, window names, and resurrect. The agent runs in
the shared background service, which does not know the pane, so the CLI plugin
`config/opencode/plugins/tmux-status/tui.ts` does the work inside the TUI. It
follows the session on screen and its subagents, and calls the shared renderer
whenever their state changes: working, a permission or question dialog up, a
turn that ended on a question, failed, or done. With session tabs on, only the
focused tab counts. The window takes the session title from the pane title.
The plugin records the session in `@oc_session`, and
`home/.tmux/resurrect-opencode.sh` restores the pane as
`opencode --session <id>`. A pane on the home screen comes back as a shell.
`python3 tests/test-opencode-tmux.py` checks the plugin's state handling, the
renderer and the save hook against a fake tmux.

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
