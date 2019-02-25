# ZSH Theme - davey
# Based on bira, gnzh, phil!'s, and nanotech

function zle-line-init zle-keymap-select {
    VI_NORMAL="NORMAL"
    VI_INSERT=""
    # VI_LEFT="{"
    # VI_RIGHT="}"
    # PR_PROMPT_BEGIN="${${KEYMAP/vicmd/$VI_LEFT}/(main|viins)/$PR_THEME$PR_LEFT_BRACE}"
    # PR_PROMPT_END="${${KEYMAP/vicmd/$VI_RIGHT}/(main|viins)/$PR_THEME$PR_RIGHT_BRACE}"
    PR_VI="${${KEYMAP/vicmd/$VI_NORMAL}/(main|viins)/$VI_INSERT}"
    zle reset-prompt
}

zle -N zle-keymap-select

function precmd {

    local TERMPLACE
    (( TERMPLACE = ${COLUMNS} - 1 ))

    ###
    # Truncate the path if it's too long.
    PR_FILLBAR=""
    PR_PWDLEN=""
    PR_GIT=$(git_prompt_info)

    local promptsize=${#${(%):---(%n@%M:%l)---()--}}
    local pwdsize=${#${(%):-%~}}
    local zero='%([BSUbfksu]|([FB]|){*})'
    local gitsize=${#${(S%%)PR_GIT//$~zero/}}

    # Calculate virtualenv prompt length
    PR_VIRENVNAME=''
    local virenvsize=0
    if [[ $VIRTUAL_ENV ]]; then
        PR_VIRENVNAME="${VIRENV_PREFIX:=(}${VIRTUAL_ENV:t}${VIRENV_POSTFIX:=) }"
        virenvsize=${#${(S%%)PR_VIRENVNAME//$~zero/}}
    fi

    ((TERMWIDTH=$TERMPLACE - $gitsize - $virenvsize))

    if [[ "$promptsize + $pwdsize + $virenvsize" -gt $TERMWIDTH ]]; then
        ((PR_PWDLEN=$TERMWIDTH - $promptsize))
    else
        PR_FILLBAR="\${(l.(($TERMWIDTH - ($promptsize + $pwdsize)))..${PR_HBAR}.)}"
    fi
}

setprompt () {

    setopt prompt_subst

    ###
    # See if we can use colors.
    autoload colors zsh/terminfo
    if [[ "$terminfo[colors]" -ge 8 ]]; then
        colors
    fi
    for color in RED GREEN YELLOW BLUE MAGENTA CYAN WHITE; do
        eval PR_$color='%{$terminfo[bold]$fg[${(L)color}]%}'
        eval PR_LIGHT_$color='%{$fg[${(L)color}]%}'
        (( count = $count + 1 ))
    done
    PR_NO_COLOUR="%{$terminfo[sgr0]%}"

    if [[ $UID -ne 0 ]]; then # normal user
        PR_THEME=$PR_BLUE
    else
        PR_THEME=$PR_RED
    fi

    ###
    # See if we can use extended characters to look nicer.
    typeset -A altchar
    set -A altchar ${(s..)terminfo[acsc]}
    PR_SET_CHARSET="%{$terminfo[enacs]%}"
    PR_HBAR=' '
    PR_HBAR_LINE='─'
    PR_ULCORNER='╭─'
    PR_LLCORNER='╰─'
    PR_LRCORNER='─╯'
    PR_URCORNER='─╮'

    #PR_ULCORNER='┌'
    #PR_LLCORNER='└'
    #PR_LRCORNER='┘'
    #PR_URCORNER='┐'

    PR_PS2='──'
    PR_CONT='%F{green}%_%f'


    ###
    # Decide if we need to set titlebar text.
    case $TERM in
    xterm*)
        PR_TITLEBAR=$'%{\e]0;%(!.-=*[ROOT]*=- | .)%n@%m:%~ | ${COLUMNS}x${LINES} | %y\a%}'
        ;;
    screen)
        PR_TITLEBAR=$'%{\e_screen \005 (\005t) | %(!.-=[ROOT]=- | .)%n@%m:%~ | ${COLUMNS}x${LINES} | %y\e\\%}'
        ;;
    *)
        PR_TITLEBAR=''
        ;;
    esac

    ###
    # Decide whether to set a screen title
    if [[ "$TERM" == "screen" ]]; then
        PR_STITLE=$'%{\ekzsh\e\\%}'
    else
        PR_STITLE=''
    fi

    ###
    # Check the UID
    if [[ $UID -ne 0 ]]; then # normal user
      PR_USER='%F{green}%n%f'
      PR_USER_OP='%F{green}%#%f'
      #PR_PROMPT='%F{white}%{>%G%}%f '
      #PR_PROMPT='%F{white}%{➤%G%}%f '
      PR_PROMPT_BEGIN='['
      PR_PROMPT_END=']'
    else # root
      PR_USER='%F{red}%n%f'
      PR_USER_OP='%F{red}%#%f'
      #PR_PROMPT='%F{red}%{>%G%}%f '
      #PR_PROMPT='%F{red}%{➤%G%}%f '
      PR_PROMPT_BEGIN='#'
      PR_PROMPT_END='#'
    fi

    ###
    # Check if we are on SSH or not
    if [[ -n "$SSH_CLIENT"  ||  -n "$SSH2_CLIENT" ]]; then
      PR_HOST='%F{red}%M:%l%f' # SSH
    else
      PR_HOST='%F{green}%M:%l%f' # no SSH
    fi

    ###
    # various prompt
    RETURN_CODE="%(?..%F{red} %? ←%f)"
    USR_HOST="${PR_USER}%F{cyan}@${PR_HOST}"
    PR_LEFT_BRACE='['
    PR_RIGHT_BRACE=']'
    #REV_PR='%F{green}%T%f %F{yellow}%D{%a, %b %d}%f'
    REV_PR='%F{green}%D{%H%M}%f %F{yellow}%D{%a, %b %d}%f'
    PR_SHLVL='%L'
    PR_HIST_NUM='%!'
    PR_JOBS='%j'

    ###
    # main prompt
    PROMPT='\
$PR_THEME$PR_ULCORNER\
$PR_BLUE(\
$PR_MAGENTA%$PR_PWDLEN<...<%~%<<\
$PR_BLUE)\
$PR_WHITE$PR_HBAR\
$PR_WHITE${(e)PR_FILLBAR}\
$PR_BLUE$PR_HBAR$PR_HBAR\
$PR_MAGENTA$PR_VIRENVNAME\
$PR_NO_COLOUR$PR_GIT\
$PR_BLUE(\
$USR_HOST\
$PR_BLUE)\
$PR_THEME$PR_URCORNER\

$PR_THEME$PR_LLCORNER\
$PR_BLUE$PR_LEFT_BRACE\
%2(L.$PR_YELLOW$PR_SHLVL.$PR_BLUE$PR_SHLVL)\
$PR_BLUE.\
%1(j.$PR_YELLOW$PR_JOBS.$PR_BLUE$PR_JOBS)\
$PR_BLUE:$PR_HIST_NUM\
$PR_BLUE$PR_RIGHT_BRACE \
$PR_THEME$PR_PROMPT_BEGIN \
$PR_NO_COLOUR'

    ###
    # right prompt
    RPROMPT='\
%F{yellow}$PR_VI\
$PR_RED$RETURN_CODE \
$PR_THEME$PR_PROMPT_END \
$REV_PR \
$PR_THEME$PR_LRCORNER\
$PR_NO_COLOUR'

    ###
    # continued input prompt
    PS2='\
$PR_THEME$PR_PS2\
$PR_BLUE(\
$PR_CONT\
$PR_BLUE)\
$PR_THEME$PR_HBAR_LINE\
$PR_THEME$PR_PROMPT_BEGIN \
$PR_NO_COLOUR'

    ###
    # extra stuff for git
    ZSH_THEME_GIT_PROMPT_PREFIX="%F{yellow}‹"
    ZSH_THEME_GIT_PROMPT_SUFFIX="%F{yellow}› %f"
    ZSH_THEME_GIT_PROMPT_DIRTY=" %F{red}*%f"
    ZSH_THEME_GIT_PROMPT_CLEAN=""

    ZSH_THEME_GIT_PROMPT_ADDED="%{$fg[green]%} ✚"
    ZSH_THEME_GIT_PROMPT_MODIFIED="%{$fg[blue]%} ✹"
    ZSH_THEME_GIT_PROMPT_DELETED="%{$fg[red]%} ✖"
    ZSH_THEME_GIT_PROMPT_RENAMED="%{$fg[magenta]%} ➜"
    ZSH_THEME_GIT_PROMPT_UNMERGED="%{$fg[yellow]%} ═"
    ZSH_THEME_GIT_PROMPT_UNTRACKED="%{$fg[cyan]%} ✭"
}

# disable default virtualenv prompt
export VIRTUAL_ENV_DISABLE_PROMPT=1
setprompt
