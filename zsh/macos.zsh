export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:$HOME/Documents/macvim/src/MacVim:/Library/TeX/texbin:/usr/local/opt/llvm/bin:/bin/flake8:/usr/local/opt/coreutils/libexec/gnubin:$PATH"

alias gcc="gcc-8"
alias g++="g++-8"
alias wolfram="/Applications/Mathematica.app/Contents/MacOS/WolframKernel"

alias vi="mvim -v -u NONE"
alias vim="mvim -v"
# alias mvim="mvim $@ > /dev/null 2>&1"

brewdeps() {
    brew list | while read cask; do echo -n $fg[blue] $cask $fg[white]; brew deps $cask | awk '{printf(" %s ", $0)}'; echo ""; done
}

test -e "${HOME}/.iterm2_shell_integration.zsh" && source "${HOME}/.iterm2_shell_integration.zsh"
