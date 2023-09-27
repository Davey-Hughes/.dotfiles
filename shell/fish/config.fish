# disable fish greeting
set -g fish_greeting

set -x SHELL /usr/bin/fish

set PATH /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin /usr/games $HOME/go/bin $HOME/.cargo/bin
set GOPATH $HOME/go

# vi keybinds
function fish_user_key_bindings
    fish_vi_key_bindings

    # unbind cancel when pressing escape in normal mode
    bind --preset -M default -e \e

    # bind ctrl-v to edit in EDITOR
    bind -M default \cv edit_command_buffer
    bind -M insert \cv edit_command_buffer

    # bind ctrl-space to accept autosuggestion
    bind -M default -k nul accept-autosuggestion
    bind -M insert -k nul accept-autosuggestion

    bind -M default j down-or-search
    bind -M default k up-or-search
end

switch $(uname)
    case Linux
        source $HOME/.dotfiles/shell/fish/linux.fish

        set linux_version $(cat /proc/version)
        switch $linux_version
            case "*arch*"
                source $HOME/.dotfiles/shell/fish/arch.fish
                set -x TMUXCONFIG $HOME/.dotfiles/term/tmux/arch.conf
            case "*MANJARO*"
                source $HOME/.dotfiles/shell/fish/manjaro.fish
                set -x TMUXCONFIG $HOME/.dotfiles/term/tmux/manjaro.conf
        end

    case Darwin
        if [ $hostname = dhughes-K44H0465N7-mbp ]
            source $HOME/.dotfiles/shell/fish/macos_flexport.fish
            set -x TMUXCONFIG $HOME/.dotfiles/term/tmux/macos_flexport.conf
        else
            source $HOME/.dotfiles/shell/fish/macos.fish
            set -x TMUXCONFIG $HOME/.dotfiles/term/tmux/macos.conf
        end
end

# set EDITOR to neovim if exists
set -x EDITOR vim
if command -q nvim
    set -x EDITOR nvim
end

if status is-interactive
    set -g fish_escape_delay_ms 10

    set -x COLORSCHEME tokyonight
    set -x COLORSCHEME_VARIANT moon

    set kitty_theme "Tokyo Night Moon"

    switch $COLORSCHEME
        case tokyonight
            switch $COLORSCHEME_VARIANT
                case night
                    set kitty_theme "Tokyo Night"
                case storm
                    set kitty_theme "Tokyo Night Storm"
                case day
                    set kitty_theme "Tokyo Night Day"
                case '*'
                    set kitty_theme "Tokyo Night Moon"
            end

        case neosolarized
            switch $COLORSCHEME_VARIANT
                case light
                    set kitty_theme "Solarized Light"
                case '*'
                    set kitty_theme "Solarized Dark - Patched"
            end
    end

    # kitty +kitten themes --reload-in=all $kitty_theme

    # open or attach tmux in session 'main' if no arguments are passed
    function tmux
        if count $argv >/dev/null
            command tmux $argv
        else
            command tmux new-session -A -s main
        end
    end

    if command -q starship
        set -x STARSHIP_LOG error
        starship init fish | source
    end

    if command -q zoxide
        zoxide init fish | source
        alias cd="z"
    end

    if command -q exa
        alias ls="exa"
        alias l="exa -lah"
    end

    if command -q direnv
        direnv hook fish | source
    end

    if command -q thefuck
        thefuck --alias | source
    end
end
