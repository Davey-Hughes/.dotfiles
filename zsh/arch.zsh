export PATH="$PATH:$HOME/.local/bin"

export C_INCLUDE_PATH="$HOME/.local/include:$C_INCLUDE_PATH"
export CPLUS_INCLUDE_PATH="$HOME/.local/include:$CPLUS_INCLUDE_PATH"

# prints stderr in red
export LD_PRELOAD="$HOME/.local/sources/stderred/build/libstderred.so${LD_PRELOAD:+:$LD_PRELOAD}"

[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

alias vi="vim -v -u NONE"
