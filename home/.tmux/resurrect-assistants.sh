#!/usr/bin/env bash
# Continuum uses the same resurrect save hook as a manual save.
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$here/resurrect-claude.sh" "${1:-}"
python3 "${CODEX_HOME:-$HOME/.codex}/hooks/tmux-session.py" --save "${1:-}"
bash "${XDG_CONFIG_HOME:-$HOME/.config}/openclaw/tmux-status/tmux-session.sh" --save "${1:-}"
exit 0
