#!/bin/sh
# agy's terminal title: the name of the conversation on screen, as Claude
# Code's title carries its /rename name. herdr reads it from there for the
# agent's tab and sidebar row (~/.tmux/herdr-status.sh,
# ~/.config/herdr/config.toml), and a terminal tab shows it too.
#
# agy has no title of its own. It runs this through "title" in settings.json
# beside it, with its status-line JSON on stdin, each time the session's state
# changes, and sets the title to what this prints.
#
# The name is conversation_title, which carries a /rename and the title agy
# makes up for a new conversation. It stays null for a conversation resumed
# with --conversation or /resume until that is renamed, so then the name is
# read from agy's conversation index, conversation_summaries.db, which sits
# one level up from this script.
#
# With no name yet this prints "agy". Printing nothing would leave the title
# alone, and herdr would take whatever the shell set before agy started as
# the session's name.

input="$(cat)"
db="${0%/*}/../conversation_summaries.db"

# One line, without control characters, trimmed.
tidy() {
    tr '[:cntrl:]' ' ' | sed 's/^ *//; s/ *$//'
}

name=''
if command -v jq >/dev/null 2>&1; then
    name="$(printf '%s' "$input" | jq -r '.conversation_title // ""' 2>/dev/null | tidy)"
    if [ -z "$name" ] && [ -f "$db" ] && command -v sqlite3 >/dev/null 2>&1; then
        id="$(printf '%s' "$input" | jq -r '.conversation_id // ""' 2>/dev/null)"
        # Checked before it goes into the query: a UUID has nothing to escape.
        case "$id" in
            '' | *[!0-9a-f-]*) ;;
            *)
                name="$(sqlite3 -readonly -batch -noheader -cmd '.timeout 500' "$db" \
                    "SELECT title FROM conversation_summaries WHERE conversation_id = '$id'" \
                    2>/dev/null | tidy)"
                ;;
        esac
    fi
fi
printf '%s' "${name:-agy}"
