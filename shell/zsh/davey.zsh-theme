# ZSH Theme - davey
# Based on bira, gnzh, phil!'s, and nanotech

function zle-line-init zle-keymap-select {
    VI_NORMAL="NORMAL"
    VI_INSERT=""
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

    PR_BLACK='%F{0}'
    PR_RED='%F{1}'
    PR_GREEN='%F{2}'
    PR_YELLOW='%F{3}'
    PR_BLUE='%F{4}'
    PR_MAGENTA='%F{5}'
    PR_CYAN='%F{6}'
    PR_WHITE='%F{7}'

    PR_BRBLACK='%B%F{8}'
    PR_BRRED='%B%F{9}'
    PR_BRGREEN='%B%F{10}'
    PR_BRYELLOW='%B%F{11}'
    PR_BRBLUE='%B%F{12}'
    PR_BRMAGENTA='%B%F{13}'
    PR_BRCYAN='%B%F{14}'
    PR_BRWHITE='%B%F{15}'

    PR_NO_COLOUR="%{$terminfo[sgr0]%}"

    if [[ $UID -ne 0 ]]; then # normal user
        PR_THEME=$PR_BRBLUE
    else
        PR_THEME=$PR_BRRED
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

    PR_PS2='──'
    PR_CONT="$PR_BRGREEN%_%f"


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
      PR_USER="$PR_BRGREEN%n%f"
      PR_USER_OP="$PR_BRGREEN%#%f"
      PR_PROMPT_BEGIN='['
      PR_PROMPT_END=']'
    else # root
      PR_USER='$PR_BRRED%n%f'
      PR_USER_OP='$PR_BRRED%#%f'
      PR_PROMPT_BEGIN='#'
      PR_PROMPT_END='#'
    fi

    ###
    # Check if we are on SSH or not
    if [[ -n "$SSH_CLIENT"  ||  -n "$SSH2_CLIENT" ]]; then
      PR_HOST="$PR_BRRED%M:%l%f" # SSH
    else
      PR_HOST="$PR_BRGREEN%M:%l%f" # no SSH
    fi

    ###
    # various prompt
    RETURN_CODE="%(?..$PR_BRRED %? ←%f)"
    USR_HOST="${PR_USER}$PR_BRCYAN@${PR_HOST}"
    PR_LEFT_BRACE='['
    PR_RIGHT_BRACE=']'
    REV_PR="$PR_BRGREEN%D{%H%M}%f $PR_BRYELLOW%D{%a, %b %d}%f"
    PR_SHLVL='%L'
    PR_HIST_NUM='%!'
    PR_JOBS='%j'

    ###
    # main prompt
    PROMPT='\
$PR_THEME$PR_ULCORNER\
$PR_BRBLUE(\
$PR_BRMAGENTA%$PR_PWDLEN<...<%~%<<\
$PR_BRBLUE)\
$PR_BRWHITE$PR_HBAR\
$PR_BRWHITE${(e)PR_FILLBAR}\
$PR_BRBLUE$PR_HBAR$PR_HBAR\
$PR_BRMAGENTA$PR_VIRENVNAME\
$PR_NO_COLOUR$PR_GIT\
$PR_BRBLUE(\
$USR_HOST\
$PR_BRBLUE)\
$PR_THEME$PR_URCORNER\

$PR_THEME$PR_LLCORNER\
$PR_BRBLUE$PR_LEFT_BRACE\
%2(L.$PR_BRYELLOW$PR_SHLVL.$PR_BRBLUE$PR_SHLVL)\
$PR_BRBLUE.\
%1(j.$PR_BRYELLOW$PR_JOBS.$PR_BRBLUE$PR_JOBS)\
$PR_BRBLUE:$PR_HIST_NUM\
$PR_BRBLUE$PR_RIGHT_BRACE \
$PR_THEME$PR_PROMPT_BEGIN \
$PR_NO_COLOUR'

    ###
    # right prompt
    RPROMPT='\
$PR_BRYELLOW$PR_VI\
$PR_BRRED$RETURN_CODE \
$PR_THEME$PR_PROMPT_END \
$REV_PR \
$PR_THEME$PR_LRCORNER\
$PR_NO_COLOUR'

    ###
    # continued input prompt
    PS2='\
$PR_THEME$PR_PS2\
$PR_BRBLUE(\
$PR_CONT\
$PR_BRBLUE)\
$PR_THEME$PR_HBAR_LINE\
$PR_THEME$PR_PROMPT_BEGIN \
$PR_NO_COLOUR'

    ###
    # extra stuff for git
    ZSH_THEME_GIT_PROMPT_PREFIX="$PR_YELLOW‹"
    ZSH_THEME_GIT_PROMPT_SUFFIX="$PR_YELLOW› %f"
    ZSH_THEME_GIT_PROMPT_DIRTY=" $PR_RED*%f"
    ZSH_THEME_GIT_PROMPT_CLEAN=""

    ZSH_THEME_GIT_PROMPT_ADDED="$PR_GREEN ✚"
    ZSH_THEME_GIT_PROMPT_MODIFIED="$PR_BLUE ✹"
    ZSH_THEME_GIT_PROMPT_DELETED="$PR_RED ✖"
    ZSH_THEME_GIT_PROMPT_RENAMED="$PR_MAGENTA ➜"
    ZSH_THEME_GIT_PROMPT_UNMERGED="$PR_YELLOW ═"
    ZSH_THEME_GIT_PROMPT_UNTRACKED="$PR_CYAN ✭"
}

# disable default virtualenv prompt
export VIRTUAL_ENV_DISABLE_PROMPT=1
setprompt
