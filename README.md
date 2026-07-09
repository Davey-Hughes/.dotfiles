# Davey Hughes' .dotfiles

A streamlined, OS-agnostic dotfiles repository powered by [GNU Stow](https://www.gnu.org/software/stow/).

## Requirements

- **GNU Stow**: Must be installed on your system to correctly symlink the dotfiles.
- **Homebrew** (Optional): If installed, the install script will automatically install packages from your OS-specific `Brewfile`.

## Repository Structure

The repository is organized cleanly by symlink target location rather than scattered top-level folders:

- `config/` - Contains all configs destined for `~/.config/` (e.g., `fish`, `kitty`, `starship.toml`, `zellij`, etc.)
- `home/` - Contains all configs destined directly for your home directory `~/` (e.g., `.zshrc`, `.zsh/`, `.tmux.conf`, `.inputrc`, etc.)
- `os/` - OS-specific configurations, GUI tools, and machine-specific scripts for `macos`, `windows`, and `steamdeck`. Also houses the OS-specific Homebrew `Brewfile`s.

## Installation

Simply clone the repository and run the install script:

```bash
git clone https://github.com/Davey-Hughes/.dotfiles.git ~/.dotfiles
cd ~/.dotfiles
./install.sh
```

**What `install.sh` does:**
1. Validates `stow` is installed.
2. Uses `stow` to automatically symlink all configurations in `config/` and `home/` into your local system without any hardcoded path conflicts.
3. Applies essential global Git configurations.
4. Auto-detects your OS (macOS or Linux) and executes `brew bundle` using the appropriate Brewfile inside `os/` (if Homebrew is installed).

## Adding New Configs
To add a new config app down the road, you no longer need to update the install script. Simply place the config app in the correct folder (`config/` or `home/`) and rerun `./install.sh`. Stow will detect the new additions and link them!

## ZSH
The custom ZSH theme included is originally based on the `bira`, `gnzh`, `phil!`'s, and `nanotech` themes.
