eval "$(/opt/homebrew/bin/brew shellenv)"

set -x SHELL /opt/homebrew/bin/fish
set -x GOBIN $GOPATH/bin

fish_add_path --path /Library/TeX/texbin
fish_add_path --path --prepend $HOME/.local/bin
