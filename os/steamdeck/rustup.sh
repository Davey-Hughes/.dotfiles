#!/bin/bash

# --no-modify-path is load-bearing, not tidiness. rustup-init appends
# `. "$CARGO_HOME/env"` to .bash_profile, .bashrc, .profile and .zshenv; those
# are stow symlinks into this repo, so it follows them and commits the change
# for you. The line it writes hardcodes an absolute path -- on this Deck,
# /home/deck/.local/share/cargo/env -- which then breaks every other machine
# that shares these dotfiles.
#
# It is also redundant. config/shell/xdg-env.sh already exports CARGO_HOME and
# puts $CARGO_HOME/bin on PATH, and says in its own comment that it exists to
# replace sourcing cargo's generated env for exactly this reason; fish gets
# there via fish_add_path in config/fish/config.fish.
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- --no-modify-path
