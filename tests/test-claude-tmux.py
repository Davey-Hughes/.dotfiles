#!/usr/bin/env python3
"""Claude Code's tmux-status ticker, on an isolated tmux server.

The ticker (tmux-status.sh --tick) animates every pane in state "working" and
clears the state of any such pane whose process is no longer an assistant.
These run it against a private server and lock directory, so neither the
user's tmux nor their live ticker is touched.
"""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / 'config/.claude/hooks/tmux-status.sh'


@unittest.skipUnless(shutil.which('tmux'), 'needs tmux')
class TickerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='claude-tmux-'))
        self.socket = self.dir / 'tmux.sock'
        self.runtime = self.dir / 'run'
        self.runtime.mkdir()

    def tearDown(self):
        subprocess.run(['tmux', '-S', str(self.socket), 'kill-server'], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def tmux(self, *args):
        return subprocess.run(['tmux', '-S', str(self.socket), *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def pane_running(self, command):
        """A pane whose pane_current_command reads `command`."""
        program = self.dir / command
        shutil.copyfile(shutil.which('sleep'), program)
        program.chmod(0o755)
        self.tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'test', str(program), '120')
        pane = self.tmux('display-message', '-p', '-t', 'test', '#{pane_id}')
        for _ in range(30):
            if self.tmux('display-message', '-p', '-t', pane, '#{pane_current_command}') == command:
                return pane
            time.sleep(0.1)
        self.fail(f'pane never showed {command}')

    def run_ticker(self, seconds=1.5):
        env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
        env['TMUX'] = f'{self.socket},0,0'
        env['XDG_RUNTIME_DIR'] = str(self.runtime)
        ticker = subprocess.Popen(['bash', str(STATUS), '--tick'], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        time.sleep(seconds)
        os.killpg(ticker.pid, signal.SIGKILL)
        ticker.wait()

    def option(self, pane, name):
        return self.tmux('display-message', '-p', '-t', pane, f'#{{{name}}}')

    def test_keeps_a_working_claude_pane_with_no_colour_yet(self):
        # refresh_state sets @cc_state and @cc_colour in two tmux calls, so on
        # every idle -> working the ticker can catch "working" with the idle
        # state's empty colour still in place. That pane is still claude's.
        pane = self.pane_running('claude')
        self.tmux('set-option', '-p', '-t', pane, '@cc_state', 'working')
        self.tmux('set-option', '-p', '-t', pane, '@cc_colour', '')
        self.tmux('set-option', '-p', '-t', pane, '@cc_name', 'cve')
        self.run_ticker()
        self.assertEqual(self.option(pane, '@cc_name'), 'cve')
        self.assertEqual(self.option(pane, '@cc_state'), 'working')

    def test_clears_a_working_pane_the_assistant_has_left(self):
        pane = self.pane_running('fish')
        self.tmux('set-option', '-p', '-t', pane, '@cc_state', 'working')
        self.tmux('set-option', '-p', '-t', pane, '@cc_colour', 'colour216')
        self.tmux('set-option', '-p', '-t', pane, '@cc_name', 'cve')
        self.run_ticker()
        self.assertEqual(self.option(pane, '@cc_state'), '')
        self.assertEqual(self.option(pane, '@cc_name'), '')


if __name__ == '__main__':
    unittest.main()
