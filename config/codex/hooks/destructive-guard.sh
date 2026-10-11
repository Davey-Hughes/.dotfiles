#!/bin/sh
# A missing interpreter or crashed adapter must produce a supported denial.
adapter="$(dirname "$0")/destructive-guard.py"
if ! python3 "$adapter"; then
    echo 'Destructive-command guard failed to run. Command blocked; repair the guard.' >&2
    exit 2
fi
