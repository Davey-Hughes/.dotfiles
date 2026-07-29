# Davey Hughes' .dotfiles

A streamlined, OS-agnostic dotfiles repository powered by [GNU Stow](https://www.gnu.org/software/stow/).

## Requirements

- **GNU Stow**: Must be installed on your system to correctly symlink the dotfiles.
- **Homebrew** (Optional): If installed, the install script will automatically install packages from your OS-specific `Brewfile`.

## Repository Structure

The repository is organized cleanly by symlink target location rather than scattered top-level folders:

- `config/` - Cross-platform configs symlinked into `$XDG_CONFIG_HOME` (defaults to `~/.config/`) — e.g. `fish`, `kitty`, `starship.toml`, `zellij`. Also `config/shell/xdg-env.sh`, the cross-shell XDG environment (see below).
- `home/` - Configs symlinked directly into your home directory `~/` — e.g. `.zshrc`, `.zshenv`, `.bashrc`, `.tmux.conf`, `.inputrc`.
- `os/<platform>/` - Platform-specific trees. A `config/` or `home/` subfolder here is stowed **only on the matching OS**, layered on top of the common configs: `macos` → `yabai`/`skhd`, `arch` → `paru`/`MangoHud`/KDE Plasma config, plus `steamdeck` (SteamOS is Arch-based, so it inherits the `arch` layer). Also houses GUI tools, machine scripts, and the Homebrew `Brewfile`s.

## Installation

Simply clone the repository and run the install script:

```bash
git clone https://git.daveynet.xyz/davey/dotfiles.git ~/.dotfiles
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

## KDE Plasma (Arch only)

KDE Plasma settings (theme, shortcuts, panels, Dolphin/Konsole/Kate) are tracked
under `os/arch/config/` and symlinked into `~/.config` on Arch/SteamOS. KDE writes
through the symlinks (KConfig `directWriteFallback`), so tweaking settings in
System Settings edits the repo copy directly.

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
./tests/run-all.sh
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
| `tests/test-install.sh` | `install.sh` failing on a machine that is not already set up. Runs it twice against a throwaway `$HOME` — stow's directory folding, the conflict/backup path, and idempotence. |

A checker that is not installed locally (`fish`, `shellcheck`, PyYAML) prints
`skip`; in CI the same absence is a hard failure, so nothing goes unchecked there.

## ZSH
The custom ZSH theme included is originally based on the `bira`, `gnzh`, `phil!`'s, and `nanotech` themes.
