export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/Users/davey/Documents/macvim/src/MacVim:/Library/TeX/texbin:/usr/local/opt/llvm/bin"

alias gcc="gcc-7"
alias wolfram="/Applications/Mathematica.app/Contents/MacOS/WolframKernel"

alias vi="mvim -v -u NONE"
alias vim="mvim -v"
# alias mvim="mvim $@ > /dev/null 2>&1"

brewdeps() {
    brew list | while read cask; do echo -n $fg[blue] $cask $fg[white]; brew deps $cask | awk '{printf(" %s ", $0)}'; echo ""; done
}
