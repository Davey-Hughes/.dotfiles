export PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games/:$HOME/.local/bin:$HOME/.toolbox/bin/"

[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

alias vi="vim -v -u NONE"
alias gcc="gcc-9"

test -d ~/.linuxbrew && eval $(~/.linuxbrew/bin/brew shellenv)
test -d /home/linuxbrew/.linuxbrew && eval $(/home/linuxbrew/.linuxbrew/bin/brew shellenv)

# Amazon developer environment
alias brazil-octane="/apollo/env/OctaneBrazilTools/bin/brazil-octane"
export PATH="/apollo/env/AmazonAwsCli/bin/:$PATH"
alias third-party-promote='~/.toolbox/bin/brazil-third-party-tool promote'
alias third-party='~/.toolbox/bin/brazil-third-party-tool'
