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
2. Uses `stow` to symlink the common `config/` (into `$XDG_CONFIG_HOME`) and `home/` (into `~`), then detects your OS and stows any matching `os/<platform>/{config,home}` layer on top.
3. Applies essential global Git configurations.
4. Auto-detects your OS (macOS or Linux) and executes `brew bundle` using the appropriate Brewfile inside `os/` (if Homebrew is installed).

## Adding New Configs
To add a new config app down the road, you no longer need to update the install script. Place a cross-platform config in `config/` (or `home/`), or a platform-specific one under `os/<platform>/config` (or `os/<platform>/home`), then rerun `./install.sh`. Stow will detect the new additions and link them on the matching OS.

**One exception.** Stow collapses a directory into a single symlink when the target does not exist — convenient, since files added later then appear without re-stowing. If the app writes back into its own config directory (state, caches, credentials, downloaded plugins), those writes land inside this repo. Add such a directory to `UNFOLD_HOME` or `UNFOLD_CONFIG` at the top of `install.sh`, and to the matching list in `tests/test-install.sh`. Directories only these dotfiles own are better left folded.

## KDE Plasma (desktop only)

KDE Plasma settings (theme, shortcuts, panels, Dolphin/Konsole/Kate) are tracked
under `os/endeavour/config/` and symlinked into `~/.config` on the desktop only.
KDE writes through the symlinks (KConfig `directWriteFallback`), so tweaking
settings in System Settings edits the repo copy directly.

That write-through is why these files are **not** in the shared `arch` layer:
they carry one machine's hardware (`kcminputrc` names a specific mouse, `kwinrc`
keys tiling to output UUIDs, the appletsrc places panels per screen), and a
second machine editing the same symlinked file would silently rewrite the
desktop's tracked config. The Steam Deck keeps stock SteamOS Plasma.

**Dependency:** `kwinrc` enables the [`krohnkite`](https://github.com/anametologin/krohnkite)
tiling script. Install it from the AUR (e.g. `paru -S kwin-scripts-krohnkite`) for
tiling to work; the config symlinks fine without it, tiling just stays inactive.

Hardware/state files (monitor layout, file-index state, KDE Connect pairing keys,
activity stats) are intentionally **not** tracked.

## Shell environment & XDG
`config/shell/xdg-env.sh` (POSIX, for `bash`/`zsh`), `config/fish/conf.d/xdg.fish` (fish), and `config/environment.d/xdg.conf` (systemd user session, Linux) define the XDG base directories and redirect many tools' cache/data/config out of `$HOME` into XDG locations. The three files are kept in sync — `tests/test-xdg-sync.sh` enforces that, and lists the one documented exemption.

The env vars only redirect *future* writes. On a fresh machine, run `scripts/xdg-migrate.sh` (dry-run) and then `scripts/xdg-migrate.sh --apply` to move any pre-existing tool dirs (`~/.cargo`, `~/.npm`, …) into their XDG homes. It is idempotent and skips anything already migrated.

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
| `tests/test-tracked-files.sh` | Anything tracked that must not be. `config/.claude/` is a live tool directory — sessions, transcripts and OAuth state sit untracked inside a tracked parent, held back by a `.gitignore` allowlist. Also verifies the `kde-wallpaper` clean filter stripped the local wallpaper paths. |
| `tests/test-syntax.sh` | A file that will not parse in the shell or program that reads it — `bash -n`, `zsh -n`, `fish --no-execute`, JSON/TOML/YAML, plus `shellcheck` at warning level on the scripts. |
| `tests/test-xdg-sync.sh` | The three XDG env files drifting apart, which silently gives one shell a different environment than the others. |
| `tests/test-docs.sh` | A path this README names that no longer exists. |
| `tests/test-install.sh` | `install.sh` failing on a machine that is not already set up. Runs it twice against a throwaway `$HOME` — the conflict/backup path, idempotence, and stow's directory folding in both directions: every shared directory comes out real, every submodule still resolves into the checkout. Its expected list mirrors `UNFOLD_HOME`/`UNFOLD_CONFIG` rather than reading them, so dropping an entry fails the build instead of silently dropping its own assertion. |

A checker that is not installed locally (`fish`, `shellcheck`, PyYAML) prints
`skip`; in CI the same absence is a hard failure, so nothing goes unchecked there.

## ZSH
The custom ZSH theme included is originally based on the `bira`, `gnzh`, `phil!`'s, and `nanotech` themes.
