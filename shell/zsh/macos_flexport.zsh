export PATH="/bin:$HOME/Documents/macvim/src/MacVim:/Library/TeX/texbin:/opt/homebrew/opt/llvm/bin:/bin/flake8:/usr/local/opt/coreutils/libexec/gnubin:/usr/local/opt/ruby/bin:$PATH"
export PATH="$HOME/bin:$HOME/.toolbox/bin:$HOME/.cargo/bin:$PATH"
export PATH="$PATH:$HOME/projects/kotlin-debug-adapter/adapter/build/install/adapter/bin"
export LIBRARY_PATH=":/usr/local/lib:$LIBRARY_PATH"

if (( !$+commands[exa] && $+commands[gls] )); then
    alias l="gls -lah --color=auto"
fi

alias gcc="gcc-11"
alias g++="g++-11"
alias wolfram="/Applications/Mathematica.app/Contents/MacOS/WolframKernel"

alias vi="vim -u NONE"

eval "$(/opt/homebrew/bin/brew shellenv)"

brewdeps() {
    brew list | while read cask; do echo -n $fg[blue] $cask $fg[white]; brew deps $cask | awk '{printf(" %s ", $0)}'; echo ""; done
}

test -e "${HOME}/.iterm2_shell_integration.zsh" && source "${HOME}/.iterm2_shell_integration.zsh"

export BUILDKITE_API_TOKEN="bkua_991c6759fa01012c09a2b6efc2d28d4c6806c0c7"
alias bastion_ssh="~/flexport/env-improvement/bin/bastion ssh"

# Nix
if [ -e '/nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh' ]; then
  . '/nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh'
fi

export PATH="$PATH:$HOME/.nix-profile/bin"
export DISABLE_NIX_SHELL_WELCOME=1

# End Nix

export GITHUB_USERNAME=dhughes

alias k="kubectl"

# Add RVM to PATH for scripting. Make sure this is the last PATH variable change.
fx() {
    (cd ~/flexport && command fx "$@")
}

export PATH="$PATH:$HOME/.rvm/bin"
alias mpr="/Users/dhughes/flexport/mpr"
alias dev="fx rdev"
alias rdev="fx rdev"

# Add RVM to PATH for scripting. Make sure this is the last PATH variable change.
export PATH="$PATH:$HOME/.rvm/bin"
