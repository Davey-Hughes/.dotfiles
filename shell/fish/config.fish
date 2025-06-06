# disable fish greeting
set -g fish_greeting

set -x SHELL /usr/bin/fish

set PATH $HOME/.cargo/bin /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin /usr/games $GOPATH $GOBIN

# vi keybinds
function fish_user_key_bindings
    fish_vi_key_bindings

    # unbind cancel when pressing escape in normal mode
    bind --preset -M default -e \e

    # bind ctrl-v to edit in EDITOR
    bind -M default \cv edit_command_buffer
    bind -M insert \cv edit_command_buffer

    # bind ctrl-space to accept autosuggestion
    bind -M default ctrl-space accept-autosuggestion
    bind -M insert ctrl-space accept-autosuggestion

    bind -M default j down-or-search
    bind -M default k up-or-search
end

switch $(uname)
    case Linux
        source $HOME/.dotfiles/shell/fish/linux.fish

        set linux_version $(cat /proc/version)
        switch $linux_version
            case "*valve*"
                source $HOME/.dotfiles/shell/fish/steamdeck.fish
                set -x TMUXCONFIG $HOME/.dotfiles/term/tmux/steamdeck.conf
            case "*arch*"
                source $HOME/.dotfiles/shell/fish/arch.fish
                set -x TMUXCONFIG $HOME/.dotfiles/term/tmux/arch.conf
            case "*MANJARO*"
                source $HOME/.dotfiles/shell/fish/manjaro.fish
                set -x TMUXCONFIG $HOME/.dotfiles/term/tmux/manjaro.conf
        end

    case Darwin
        # custom for another macos machine
        if [ $hostname = "" ]
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

    set -g fish_cursor_default block
    set -g fish_cursor_insert block

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

    if command -q eza
        alias ls="eza"
        alias l="eza -lah"
    end

    if command -q direnv
        direnv hook fish | source

        # silence direnv output
        set -x DIRENV_LOG_FORMAT
    end

    if command -q thefuck
        thefuck --alias | source
    end

    if command -q atuin
        set -x ATUIN_NOBIND true
        atuin init fish | source

        bind \cr _atuin_search
        bind up _atuin_bind_up
        bind \eOA _atuin_bind_up
        bind \e\[A _atuin_bind_up

        if bind -M insert >/dev/null 2>&1
            bind -M insert \cr _atuin_search
            bind -M insert up _atuin_bind_up
            bind -M insert \eOA _atuin_bind_up
            bind -M insert \e\[A _atuin_bind_up
        end
    end

    if command -q yazi
        function y
            set tmp (mktemp -t "yazi-cwd.XXXXXX")
            yazi $argv --cwd-file="$tmp"
            if set cwd (command cat -- "$tmp"); and [ -n "$cwd" ]; and [ "$cwd" != "$PWD" ]
                builtin cd -- "$cwd"
            end
            rm -f -- "$tmp"
        end
    end

    if test -e $HOME/.config/claude/claude.txt
        cat $HOME/.config/claude/claude.txt | read -x ANTHROPIC_API_KEY
    end

end
