#!/usr/bin/env python3
"""Verify real tmux renaming on an isolated server, without Codex API calls."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('tmux'), 'needs tmux')
class NamingTests(unittest.TestCase):
    def test_native_title_and_manual_window_name(self):
        with tempfile.TemporaryDirectory(prefix='codex-tmux-names-') as directory:
            directory = Path(directory)
            socket = directory / 'tmux.sock'
            codex = directory / 'codex'
            shutil.copyfile(shutil.which('sleep'), codex)
            codex.chmod(0o755)
            def tmux(*args):
                return subprocess.run(['tmux', '-S', str(socket), *args],
                                      capture_output=True, text=True, check=True).stdout.strip()
            try:
                tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'test', str(codex), '120')
                config = (ROOT / 'home/.tmux.conf').read_text()
                naming = config[config.index('set -g automatic-rename-format '):
                                config.index("run -b '~/.config/.claude/hooks/tmux-status.sh names'")]
                fixture = directory / 'naming.conf'
                fixture.write_text(naming)
                tmux('source-file', str(fixture))
                tmux('set-window-option', '-t', 'test:0', 'automatic-rename', 'on')
                def expect(name):
                    for _ in range(30):
                        actual = tmux('display-message', '-p', '-t', 'test:0', '#{window_name}')
                        if actual == name:
                            return
                        time.sleep(0.1)
                    self.assertEqual(actual, name)
                tmux('select-pane', '-t', 'test:0.0', '-T', 'Port Codex config')
                expect('Port Codex config')
                tmux('select-pane', '-t', 'test:0.0', '-T', 'Renamed chat')
                expect('Renamed chat')
                tmux('rename-window', '-t', 'test:0', 'My manual name')
                tmux('select-pane', '-t', 'test:0.0', '-T', 'Another chat')
                time.sleep(0.3)
                expect('My manual name')
            finally:
                subprocess.run(['tmux', '-S', str(socket), 'kill-server'], capture_output=True)

if __name__ == '__main__':
    unittest.main()
