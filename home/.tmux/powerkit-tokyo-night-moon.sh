#!/usr/bin/env bash
# Tokyo Night Moon for tmux-powerkit, which ships only Night, Storm and Day.
# Ghostty runs tokyonight-moon, so this is powerkit's tokyo-night/night.sh with
# each color swapped for its role's Moon value (folke/tokyonight.nvim,
# lua/tokyonight/colors/moon.lua).
#
# One exception: the active window keeps Night's purple, #9d7cd8. Moon's purple
# is a pink (#fca7ea) and its magenta (#c099ff) puts the white tab label at 2.2
# contrast instead of 3.3. herdr's active row is blended from this purple
# (~/.config/herdr/config.toml), and ~/.config/.claude/hooks/tmux-status.sh
# drops its state colors on the active tab because of it.
#
# powerkit caches theme colors for a day under ~/.cache/tmux-powerkit/data,
# keyed by theme and variant. After an edit here, delete
# ~/.cache/tmux-powerkit/data/theme_colors__custom__moon, then prefix+R.

# shellcheck disable=SC2034  # read by powerkit's theme loader
declare -gA THEME_COLORS=(
    # Core: the terminal background, for transparent-mode separators.
    [background]="#222436"               # bg

    # Status bar
    [statusbar-bg]="#2f334d"             # bg_highlight
    [statusbar-fg]="#c8d3f5"             # fg

    # Session (status-left)
    [session-bg]="#c3e88d"               # green
    [session-fg]="#2f334d"               # bg_highlight
    [session-prefix-bg]="#ffc777"        # yellow
    [session-copy-bg]="#86e1fc"          # cyan
    [session-search-bg]="#c3e88d"        # green
    [session-command-bg]="#c099ff"       # magenta

    # Windows. powerkit derives a -lighter variant (index background) and a
    # -darker one (text) from each base.
    [window-active-base]="#9d7cd8"       # Night purple, see above
    [window-active-style]="bold"
    [window-inactive-base]="#3b4261"     # fg_gutter
    [window-inactive-style]="none"

    # Window state
    [window-activity-style]="italics"
    [window-bell-style]="bold"
    [window-zoomed-bg]="#86e1fc"         # cyan

    # Panes
    [pane-border-active]="#82aaff"       # blue
    [pane-border-inactive]="#3b4261"     # fg_gutter

    # Plugin health states
    [ok-base]="#394b70"                  # blue7
    [good-base]="#c3e88d"                # green
    [info-base]="#86e1fc"                # cyan
    [warning-base]="#ffc777"             # yellow
    [error-base]="#ff757f"               # red
    [disabled-base]="#636da6"            # comment

    # Messages
    [message-bg]="#2f334d"               # bg_highlight
    [message-fg]="#c8d3f5"               # fg

    # Popups and menus
    [popup-bg]="#2f334d"                 # bg_highlight
    [popup-fg]="#c8d3f5"                 # fg
    [popup-border]="#82aaff"             # blue
    [menu-bg]="#2f334d"                  # bg_highlight
    [menu-fg]="#c8d3f5"                  # fg
    [menu-selected-bg]="#c3e88d"         # green
    [menu-selected-fg]="#2f334d"         # bg_highlight
    [menu-border]="#82aaff"              # blue
)
