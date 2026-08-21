# distrobox-export drops its wrappers here (see os/steamdeck/container-setup.sh),
# so the container toolchain -- nvim, yazi, lazygit, btm -- is only callable from
# the host once this is on PATH. --prepend so an exported binary wins over a
# stale host copy of the same name left behind by an older install.
#
# arch.fish has the identical line, but config.fish sources exactly one of the
# two: /proc/version says "valve" on SteamOS and that branch lands here, never
# there. Removing this does not fall back to arch.fish; it just breaks nvim.
fish_add_path --prepend --path $HOME/.local/bin

alias ssh="TERM=xterm-256color command ssh"
