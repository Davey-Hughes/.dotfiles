# path to oh-my-zsh installation
export ZSH=$HOME/.oh-my-zsh

# theme
ZSH_THEME="davey"
COLORSCHEME="tokyonight-moon"

case $COLORSCHEME in
    tokyonight-moon)
        # kitty +kitten themes --reload-in=all Tokyo Night Moon
    ;;
    neosolarized)
    ;&
    *)
        # kitty +kitten themes --reload-in=all Solarized Dark - Patched
    ;;
esac


DEFAULT_USER="davey"

COMPLETION_WAITING_DOTS="true"

# don't need to explicitly put . for file autocompletion
setopt globdots

# perform implicit tees or cats when multiple redirections are attempted
setopt multios

# don't automatically overwrite existing file. Use >! to overwrite
setopt noclobber

# allow comments in interactive mode
setopt interactivecomments

# don't add duplicate commands to the histfile
setopt HIST_IGNORE_ALL_DUPS

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:$HOME/go/bin"
export GOPATH="$HOME/go"
export LANG="en_US.UTF-8"

source $ZSH/oh-my-zsh.sh
fpath=(/usr/local/share/zsh-completions $fpath)

export LANG=en_US.UTF-8

# source os specific settings
case `uname` in
    Darwin)
        hostname=$(hostname)

        if [[ $hostname == "dhughes-K44H0465N7-mbp" ]]; then
            source $HOME/.dotfiles/shell/zsh/macos_flexport.zsh
            export TMUXCONFIG="$HOME/.dotfiles/term/tmux/macos_flexport.conf"
        else
            source $HOME/.dotfiles/shell/zsh/macos.zsh
            export TMUXCONFIG="$HOME/.dotfiles/term/tmux/macos.conf"
        fi

    ;;
    Linux)
        # common linux zsh configs
        source $HOME/.dotfiles/shell/zsh/linux.zsh

        version=$(cat /proc/version)
        if [[ $version =~ "arch" ]]; then
            source $HOME/.dotfiles/shell/zsh/arch.zsh
            export TMUXCONFIG="$HOME/.dotfiles/term/tmux/arch.conf"
        elif [[ $version =~ "MANJARO" ]]; then
            source $HOME/.dotfiles/shell/zsh/manjaro.zsh
            export TMUXCONFIG="$HOME/.dotfiles/term/tmux/manjaro.conf"
        elif [[ $version =~ "Red Hat" ]]; then
            source $HOME/.dotfiles/shell/zsh/rhel.zsh
            export TMUXCONFIG="$HOME/.dotfiles/term/tmux/rhel.conf"
        elif [[ $version =~ "WSL2" ]]; then
            source $HOME/.dotfiles/shell/zsh/wsl.zsh
            export TMUXCONFIG="$HOME/.dotfiles/term/tmux/wsl.conf"
        else
            source $HOME/.dotfiles/shell/zsh/ubuntu.zsh
            export TMUXCONFIG="$HOME/.dotfiles/term/tmux/ubuntu.conf"
        fi
    ;;
    FreeBSD)
    ;;
esac

# open or attach tmux in session 'main' if no arguments are passed
tmux() {
    if [[ $# -eq 0 ]]; then
        command tmux new-session -A -s main
    else
        command tmux $@
    fi
}


alias zshconfig="vim ~/.zshrc"
alias gdb="gdb -q"

alias :q="echo \"You're not in vim\""
alias :w="echo \"You're not in vim\""
alias :wq="echo \"You're not in vim\""

# if (( $+commands[starship] )); then
    # eval "$(starship init zsh)"
# fi

if (( $+commands[exa] )); then
    alias ls="exa"
    alias l="exa -lah"
else
    # get ANSI solarized colors for ls
    source $HOME/.dotfiles/shell/zsh/dircolors/lscolors.zsh
    export LS_COLORS
fi

if (( $+commands[zoxide] )); then
    eval "$(zoxide init zsh)"
    alias cd="z"
fi

if (( $+commands[direnv] )); then
    eval "$(direnv hook zsh)"
fi

if (( $+commands[thefuck] )); then
    eval $(thefuck --alias)
fi

# set editor to nvim if available, else vim
export EDITOR='vim'
if (( $+commands[nvim] )); then
    export EDITOR='nvim'
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

# use arrows and jk to search up or down
bindkey '^[[A' up-line-or-search
bindkey '^[[B' down-line-or-search

bindkey -M vicmd 'k' up-line-or-beginning-search
bindkey -M vicmd 'j' down-line-or-beginning-search

# allow ctrl-a and ctrl-e to move to beginning/end of line
bindkey '^a' beginning-of-line
bindkey '^e' end-of-line

# fish-like auto suggestions - use ctrl-space to accept suggestion
source $HOME/.dotfiles/shell/zsh/zsh-autosuggestions/zsh-autosuggestions.zsh
bindkey '^ ' autosuggest-accept
ZSH_AUTOSUGGEST_ACCEPT_WIDGETS=()


# history
HISTFILE=~/.histfile
HISTSIZE=10000
SAVEHIST=10000
setopt appendhistory autocd extendedglob nomatch notify
unsetopt beep

zstyle :compinstall filename '$HOME/.zshrc'

autoload -Uz compinit
compinit -d $HOME/.cache/zsh/zcompdump-$ZSH_VERSION

zstyle ':completion:*' special-dirs false
_comp_options+=(globdots)

export GITHUB_USERNAME=dhughes

alias mpr="/Users/dhughes/flexport/mpr"
alias dev="fx rdev"
