export PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games/:$HOME/.local/bin"

export LD_PRELOAD="$HOME/.local/sources/stderred/build/libstderred.so${LD_PRELOAD:+:$LD_PRELOAD}"

TZ='America/New_York'; export TZ

[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

alias vi="vim -v -u NONE"
alias gcc="gcc-9"

test -d ~/.linuxbrew && eval $(~/.linuxbrew/bin/brew shellenv)
test -d /home/linuxbrew/.linuxbrew && eval $(/home/linuxbrew/.linuxbrew/bin/brew shellenv)
