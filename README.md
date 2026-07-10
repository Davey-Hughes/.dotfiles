# Davey Hughes' .dotfiles

A streamlined, OS-agnostic dotfiles repository powered by [GNU Stow](https://www.gnu.org/software/stow/).

## Requirements

- **GNU Stow**: Must be installed on your system to correctly symlink the dotfiles.
- **Homebrew** (Optional): If installed, the install script will automatically install packages from your OS-specific `Brewfile`.

## Repository Structure

The repository is organized cleanly by symlink target location rather than scattered top-level folders:

- `config/` - Cross-platform configs symlinked into `$XDG_CONFIG_HOME` (defaults to `~/.config/`) — e.g. `fish`, `kitty`, `starship.toml`, `zellij`. Also `config/shell/xdg-env.sh`, the cross-shell XDG environment (see below).
- `home/` - Configs symlinked directly into your home directory `~/` — e.g. `.zshrc`, `.zshenv`, `.bashrc`, `.tmux.conf`, `.inputrc`.
- `os/<platform>/` - Platform-specific trees. A `config/` or `home/` subfolder here is stowed **only on the matching OS**, layered on top of the common configs: `macos` → `yabai`/`skhd`, `arch` → `paru`/`MangoHud`, plus `steamdeck`. Also houses GUI tools, machine scripts, and the Homebrew `Brewfile`s.

## Installation

Simply clone the repository and run the install script:

```bash
git clone https://github.com/Davey-Hughes/.dotfiles.git ~/.dotfiles
cd ~/.dotfiles
./install.sh
```

**What `install.sh` does:**
1. Validates `stow` is installed.
2. Uses `stow` to symlink the common `config/` (into `$XDG_CONFIG_HOME`) and `home/` (into `~`), then detects your OS and stows any matching `os/<platform>/{config,home}` layer on top.
3. Applies essential global Git configurations.
4. Auto-detects your OS (macOS or Linux) and executes `brew bundle` using the appropriate Brewfile inside `os/` (if Homebrew is installed).

## Adding New Configs
To add a new config app down the road, you no longer need to update the install script. Place a cross-platform config in `config/` (or `home/`), or a platform-specific one under `os/<platform>/config` (or `os/<platform>/home`), then rerun `./install.sh`. Stow will detect the new additions and link them on the matching OS.

## Shell environment & XDG
`config/shell/xdg-env.sh` (POSIX, for `bash`/`zsh`) and `config/fish/conf.d/xdg.fish` (fish) define the XDG base directories and redirect many tools' cache/data/config out of `$HOME` into XDG locations. The two files are kept in sync. See `docs/xdg-home-audit.md` for the full inventory of what is redirected versus intentionally left in place.

## ZSH
The custom ZSH theme included is originally based on the `bira`, `gnzh`, `phil!`'s, and `nanotech` themes.
