# disable fish greeting
set -g fish_greeting

set -x SHELL /usr/bin/fish

# Add paths cleanly. -gP writes to $PATH directly rather than to the universal
# fish_user_paths, so this file stays the only source of truth: a path removed
# here is gone next shell instead of lingering in local universal state.
# Duplicates and nonexistent directories are ignored.
fish_add_path -gP $CARGO_HOME/bin $BUN_INSTALL/bin /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin /usr/games $GOPATH/bin $GOBIN

# Personal utilities: daveyutils' `make` symlinks the scripts and the compiled
# binaries into ./bin. Absent until `make` has been run there.
fish_add_path -gP $HOME/projects/daveyutils/bin

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

switch (uname)
    case Linux
        source $__fish_config_dir/linux.fish

        set linux_version (cat /proc/version)
        switch $linux_version
            case "*valve*"
                source $__fish_config_dir/steamdeck.fish
                set -x TMUXCONFIG $HOME/.tmux/steamdeck.conf
            case "*arch*"
                source $__fish_config_dir/arch.fish
                set -x TMUXCONFIG $HOME/.tmux/arch.conf
        end

    case Darwin
        # custom for another macos machine
        if test -n "$hostname"
            source $__fish_config_dir/macos.fish
            set -x TMUXCONFIG $HOME/.tmux/macos.conf
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

    if command -q lazygit
        alias gg="lazygit"
    end

    if test -d $XDG_CONFIG_HOME/.claude
        set -x CLAUDE_CONFIG_DIR $XDG_CONFIG_HOME/.claude
    end

    # if test -e $XDG_CONFIG_HOME/.claude/claude-api.txt
    #     cat $XDG_CONFIG_HOME/.claude/claude-api.txt | read -x CLAUDE_API_KEY
    # end
    #
    # if test -e $XDG_CONFIG_HOME/.claude/claude-code.txt
    #     cat $XDG_CONFIG_HOME/.claude/claude-code.txt | read -x CLAUDE_CODE_OAUTH_TOKEN
    # end

    if test -e $XDG_CONFIG_HOME/.gemini/gemini-api.txt
        read -x GEMINI_API_KEY <$XDG_CONFIG_HOME/.gemini/gemini-api.txt
    end

    # make sure docker context is default even if docker desktop is open
    set -x DOCKER_CONTEXT default

    source $__fish_config_dir/fzf-tokyonight.fish
end

set -gx ANTIGRAVITY_CONFIG_DIR "$XDG_CONFIG_HOME/.gemini/antigravity-cli"
