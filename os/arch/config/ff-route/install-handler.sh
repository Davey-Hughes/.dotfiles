#!/usr/bin/env bash
# Register ff-route as the default http/https handler.
#
# Run once per machine, after ./install.sh has stowed the symlinks. Stow places
# the files, but the actual association lives in ~/.config/mimeapps.list, which
# is machine state and deliberately not tracked. Idempotent.

set -euo pipefail

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ROUTER="$HOME/.local/bin/ff-route"

[ -x "$ROUTER" ] || { echo "ff-route not found at $ROUTER — run ./install.sh first" >&2; exit 1; }
[ -e "$APPS/ff-route.desktop" ] || { echo "ff-route.desktop not stowed into $APPS" >&2; exit 1; }

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS"
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1

xdg-settings set default-web-browser ff-route.desktop
xdg-mime default ff-route.desktop x-scheme-handler/http
xdg-mime default ff-route.desktop x-scheme-handler/https

echo "default browser: $(xdg-settings get default-web-browser)"
echo "https handler:   $(xdg-mime query default x-scheme-handler/https)"
echo
echo "Verify with:  ff-route --test"
echo "Restart Slack so it picks up FF_PROFILE=beaver from the stowed slack.desktop."
