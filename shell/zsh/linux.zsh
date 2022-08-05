# linuxbrew
test -d ~/.linuxbrew && eval $(~/.linuxbrew/bin/brew shellenv)
test -d /home/linuxbrew/.linuxbrew && eval $(/home/linuxbrew/.linuxbrew/bin/brew shellenv)

alias vi="vim -v -u NONE"

# prints stderr in red
test -d "~/.local/sources/stderred" && export LD_PRELOAD="$HOME/.local/sources/stderred/build/libstderred.so${LD_PRELOAD:+:$LD_PRELOAD}"

# fzf
[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

# TZ='America/New_York'; export TZ
