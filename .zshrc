# Path to your oh-my-zsh installation.
export ZSH=$HOME/.oh-my-zsh

# theme
ZSH_THEME="davey"

DEFAULT_USER="davey"

# Uncomment the following line to display red dots whilst waiting for completion.
COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Which plugins would you like to load? (plugins can be found in ~/.oh-my-zsh/plugins/*)
# Custom plugins may be added to ~/.oh-my-zsh/custom/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
# plugins=(git)

# User configuration

# don't need to explicitly put . for file autocompletion
setopt globdots

# perform implicit tees or cats when multiple redirections are attempted
setopt multios

# don't automatically overwrite existing file. Use >! to overwrite
setopt noclobber

# allow comments in interactive mode
setopt interactivecomments

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games"
export LANG="en_US.UTF-8"

source $ZSH/oh-my-zsh.sh
fpath=(/usr/local/share/zsh-completions $fpath)

# You may need to manually set your language environment
export LANG=en_US.UTF-8

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.

# source os specific settings
# source $ZSH/omz-davey/macos.zsh

alias zshconfig="vim ~/.zshrc"
# alias make="make -j"
alias gdb="gdb -q"

alias :q="echo \"You're not in vim\""
alias :w="echo \"You're not in vim\""
alias :wq="echo \"You're not in vim\""

if ! type "$direnv" > /dev/null; then
    eval "$(direnv hook zsh)"
fi

if ! type "$thefuck" > /dev/null; then
    eval $(thefuck --alias)
fi

# vim bindings
bindkey -v
export KEYTIMEOUT=1

zle -N edit-command-line

# allow v to edit the command line (standard behaviour)
autoload -Uz edit-command-line
bindkey -M vicmd '^v' edit-command-line

# allow ctrl-p, ctrl-n for navigate history (standard behaviour)
bindkey '^P' up-history
bindkey '^N' down-history

# allow ctrl-h, ctrl-w, ctrl-? for char and word deletion (standard behaviour)
bindkey '^?' backward-delete-char
bindkey '^h' backward-delete-char
bindkey '^w' backward-kill-word

# allow ctrl-r to perform backward search in history
bindkey '^r' history-incremental-search-backward

# allow ctrl-a and ctrl-e to move to beginning/end of line
bindkey '^a' beginning-of-line
bindkey '^e' end-of-line

# Lines configured by zsh-newuser-install
HISTFILE=~/.histfile
HISTSIZE=10000
SAVEHIST=10000
setopt appendhistory autocd extendedglob nomatch notify
unsetopt beep
# End of lines configured by zsh-newuser-install
# The following lines were added by compinstall
zstyle :compinstall filename '$HOME/.zshrc'

autoload -Uz compinit
compinit

test -e "${HOME}/.iterm2_shell_integration.zsh" && source "${HOME}/.iterm2_shell_integration.zsh"
