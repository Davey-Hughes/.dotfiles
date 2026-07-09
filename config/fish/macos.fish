eval "$(/opt/homebrew/bin/brew shellenv)"

set -x SHELL /opt/homebrew/bin/fish
set -x GOBIN $HOME/go/bin

fish_add_path --path /Library/TeX/texbin
