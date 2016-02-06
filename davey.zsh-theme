# ZSH Theme - davey
# Based on bira, gnzh, phil!'s, and nanotech

function precmd {

    local TERMPLACE
    (( TERMPLACE = ${COLUMNS} - 1 ))

    ###
    # Truncate the path if it's too long.
    PR_FILLBAR=""
    PR_PWDLEN=""
    PR_GIT=$(git_prompt_info)

    local promptsize=${#${(%):---(%n@%m:%l)---()--}}
    local pwdsize=${#${(%):-%~}}
    local zero='%([BSUbfksu]|([FB]|){*})'
    local gitsize=${#${(S%%)PR_GIT//$~zero/}}

    ((TERMWIDTH=$TERMPLACE - $gitsize))

    if [[ "$promptsize + $pwdsize" -gt $TERMWIDTH ]]; then
	    ((PR_PWDLEN=$TERMWIDTH - $promptsize))
    else
        PR_FILLBAR="\${(l.(($TERMWIDTH - ($promptsize + $pwdsize)))..${PR_HBAR}.)}"
    fi

    ###
    # Get APM info.
    if which ibam > /dev/null; then
        PR_APM_RESULT=`ibam --percentbattery`
    elif which apm > /dev/null; then
        PR_APM_RESULT=`apm`
    fi
}

setopt extended_glob
preexec () {
    if [[ "$TERM" == "screen" ]]; then
        local CMD=${1[(wr)^(*=*|sudo|-*)]}
        echo -n "\ek$CMD\e\\"
    fi
}

setprompt () {

    setopt prompt_subst

    ###
    # APM detection
    if which ibam > /dev/null; then
        PR_APM='$PR_RED${${PR_APM_RESULT[(f)1]}[(w)-2]}%%(${${PR_APM_RESULT[(f)3]}[(w)-1]})$PR_LIGHT_BLUE:'
    elif which apm > /dev/null; then
        PR_APM='$PR_RED${PR_APM_RESULT[(w)5,(w)6]/\% /%%}$PR_LIGHT_BLUE:'
    else
        PR_APM=''
    fi

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
    RETURN_CODE="%(?..%F{red}%? ←%f)"
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

setprompt
