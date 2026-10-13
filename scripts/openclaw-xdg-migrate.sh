#!/usr/bin/env bash
#
# Move an existing ~/.openclaw install into the XDG locations set by
# config/shell/xdg-env.sh, and wire the tracked $include fragments into the
# machine-local openclaw.json:
#
#   ~/.openclaw/openclaw.json   -> $OPENCLAW_CONFIG_PATH  (~/.config/openclaw/openclaw.json)
#   ~/.openclaw/                -> $OPENCLAW_STATE_DIR    (~/.local/share/openclaw)
#
# A running gateway holds its SQLite state open, so this refuses to move it
# while a local openclaw-gateway unit is active. Stop that first, from your own
# terminal rather than an OpenClaw chat: stopping the gateway ends the session
# that would be running this. Nothing is started again afterwards.
#
# Also the bootstrap for a fresh machine: with no ~/.openclaw it only adds the
# $include lines to an existing openclaw.json (run `openclaw onboard` first).
#
# Dry-run by default, like scripts/xdg-migrate.sh -- pass --apply to act.
# Requires install.sh to have run (env files, unfolded config dir).

set -euo pipefail

APPLY=0
case "${1:-}" in
  --apply) APPLY=1 ;;
  ""|--dry-run) APPLY=0 ;;
  *) echo "usage: $0 [--apply]" >&2; exit 2 ;;
esac

: "${XDG_CONFIG_HOME:=$HOME/.config}"
ENV_FILE="$XDG_CONFIG_HOME/shell/xdg-env.sh"
if [ ! -r "$ENV_FILE" ]; then
  echo "error: $ENV_FILE not found -- run install.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

OLD="$HOME/.openclaw"
STATE="${OPENCLAW_STATE_DIR:?}"
CFG="${OPENCLAW_CONFIG_PATH:?}"
CFG_DIR=$(dirname "$CFG")
UNIT=openclaw-gateway.service

say() { printf '  %s\n' "$*"; }
run() { # echo in dry-run, execute with --apply
  if [ "$APPLY" -eq 1 ]; then "$@"; else say "would run: $*"; fi
}
die() { echo "error: $*" >&2; exit 1; }

# --- preconditions -------------------------------------------------------------
[ -L "$CFG_DIR" ] && die "$CFG_DIR is a symlink; OpenClaw needs a real directory there (UNFOLD_CONFIG in install.sh)"
[ -L "$CFG_DIR/skills.json5" ] || die "$CFG_DIR/skills.json5 is not stowed -- run install.sh first"

if [ "$APPLY" -eq 1 ]; then echo "OpenClaw XDG migration (APPLY):"; else echo "OpenClaw XDG migration (dry-run):"; fi

# --- 1. relocate an existing ~/.openclaw ---------------------------------------
if [ -d "$OLD" ] && [ ! -L "$OLD" ]; then
  [ -e "$STATE" ] && die "$STATE already exists; refusing to merge two state dirs"
  [ -e "$CFG" ] && die "$CFG already exists; refusing to overwrite it"

  if systemctl --user is-active --quiet "$UNIT" 2>/dev/null; then
    die "$UNIT is running and holds the state open; stop it first: systemctl --user stop $UNIT"
  fi
  run mkdir -p "$(dirname "$STATE")" "$CFG_DIR"
  run mv "$OLD" "$STATE"
  # The config and its .bak ring belong to the config dir, not the state dir.
  for f in openclaw.json openclaw.json.bak openclaw.json.bak.{1..9}; do
    if [ "$APPLY" -eq 1 ]; then
      if [ -e "$STATE/$f" ]; then mv "$STATE/$f" "$CFG_DIR/$f"; fi
    elif [ -e "$OLD/$f" ]; then
      say "would move: $OLD/$f -> $CFG_DIR/$f"
    fi
  done
  MOVED=1
else
  say "no $OLD directory -- nothing to relocate"
  MOVED=0
fi

# --- 2. rewrite paths and wire in the tracked fragments -----------------------
# Absolute paths the wizard wrote (agents.*.workspace, agentDir) still name the
# old state dir; repoint them. Then add `skills: { $include }` unless the config
# already authors a skills section, which would need merging by hand.
if [ "$APPLY" -eq 1 ] && [ -f "$CFG" ]; then
  python3 - "$CFG" "$OLD" "$STATE" <<'EOF'
import json, os, sys
cfg, old, new = sys.argv[1:]
with open(cfg, encoding="utf-8") as fh:
    data = json.load(fh)  # the wizard writes plain JSON; hand-edited JSON5 needs a manual edit

def repoint(node):
    if isinstance(node, dict):
        return {k: repoint(v) for k, v in node.items()}
    if isinstance(node, list):
        return [repoint(v) for v in node]
    if isinstance(node, str) and (node == old or node.startswith(old + "/")):
        return new + node[len(old):]
    return node

data = repoint(data)
skills = data.get("skills")
if skills is None:
    data["skills"] = {"$include": "./skills.json5"}
    print("  added skills.$include")
elif skills != {"$include": "./skills.json5"}:
    print("  WARNING: config already has a skills section; merge it into skills.json5 by hand")

tmp = cfg + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, cfg)
EOF
elif [ -f "$CFG" ] || [ "$MOVED" -eq 1 ]; then
  say "would repoint $OLD/* paths in $CFG and add skills.\$include"
else
  say "no $CFG yet -- run 'openclaw onboard', then rerun this"
fi

if [ "$MOVED" -eq 1 ]; then
  echo ""
  echo "Next: openclaw doctor && openclaw status"
fi

if [ "$APPLY" -eq 0 ]; then
  echo ""
  echo "Re-run with --apply to perform the migration."
fi
