# Bare `openclaw` picks the TUI's target itself, and with a remote Gateway it
# falls back to the local embedded runtime. Send it to the Gateway instead.
function openclaw --description 'openclaw; bare, opens the Gateway TUI'
    if test (count $argv) -eq 0
        command openclaw tui
    else
        command openclaw $argv
    end
end
