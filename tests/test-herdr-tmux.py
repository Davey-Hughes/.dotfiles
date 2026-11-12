#!/usr/bin/env python3
"""herdr's agent states on the tmux tab herdr runs in, on an isolated tmux server.

home/.tmux/herdr-status.sh asks each herdr session for its agents and hands the
most urgent state to tmux-status.sh for the tmux pane showing that session.
These run one pass of it against a private tmux server, with a stand-in herdr
client in the pane and a stand-in `herdr` CLI on PATH that prints canned agent
lists, so neither the user's tmux nor their herdr server is touched.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'home/.tmux/herdr-status.sh'
STATUS = ROOT / 'config/.claude/hooks/tmux-status.sh'
BLOCK_RED = '#f7768e'

# Prints $HERDR_STUB/<session>.json for `herdr [--session NAME] agent list`,
# and fails when there is no such file, the way an unreachable server does.
CLI_STUB = '''#!/bin/sh
session=default
if [ "$1" = --session ]; then session=$2; shift 2; fi
[ "$1 $2" = "agent list" ] || exit 2
[ -f "$HERDR_STUB/$session.json" ] || exit 1
cat "$HERDR_STUB/$session.json"
'''


@unittest.skipUnless(shutil.which('tmux') and shutil.which('bash'), 'needs tmux and bash')
class HerdrBridgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='herdr-tmux-'))
        self.socket = self.dir / 'tmux.sock'
        self.runtime = self.dir / 'run'
        self.runtime.mkdir()
        self.stub = self.dir / 'stub'
        self.stub.mkdir()
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        cli = self.bin / 'herdr'
        cli.write_text(CLI_STUB)
        cli.chmod(0o755)
        self.started = False

    def tearDown(self):
        subprocess.run(['tmux', '-S', str(self.socket), 'kill-server'], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def tmux(self, *args):
        return subprocess.run(['tmux', '-S', str(self.socket), *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ('TMUX', 'TMUX_PANE') and not k.startswith('HERDR_')}
        env['TMUX'] = f'{self.socket},0,0'
        env['XDG_RUNTIME_DIR'] = str(self.runtime)
        env['PATH'] = f'{self.bin}:{env.get("PATH", "/usr/bin:/bin")}'
        env['HERDR_STUB'] = str(self.stub)
        return env

    def pane(self, name, *args):
        """A pane whose process is called `name`, with `args` on its command line.

        A copy of bash under that name, so it accepts any trailing arguments
        (they become $0, $1...) and stays up; the `; :` keeps bash from
        exec'ing sleep in its place.
        """
        program = self.dir / 'clients' / name
        program.parent.mkdir(exist_ok=True)
        if not program.exists():
            shutil.copyfile(shutil.which('bash'), program)
            program.chmod(0o755)
        command = [str(program), '-c', 'sleep 120; :', *args]
        if not self.started:
            self.tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'test', *command)
            self.started = True
            pane = self.tmux('display-message', '-p', '-t', 'test', '#{pane_id}')
        else:
            pane = self.tmux('new-window', '-d', '-P', '-F', '#{pane_id}', '-t', 'test', *command)
        for _ in range(30):
            if self.tmux('display-message', '-p', '-t', pane, '#{pane_current_command}') == name:
                return pane
            time.sleep(0.1)
        self.fail(f'pane never showed {name}')

    def agents(self, session, *statuses, title='claude'):
        """What `herdr agent list` prints for `session`, compact like herdr's."""
        listing = {'id': 'cli:agent:list', 'result': {'agents': [
            {'agent': 'claude', 'agent_status': status, 'pane_id': f'w1:p{i}',
             'terminal_title': title}
            for i, status in enumerate(statuses)], 'type': 'agent_list'}}
        (self.stub / f'{session}.json').write_text(json.dumps(listing, separators=(',', ':')))

    def server_down(self, session):
        (self.stub / f'{session}.json').unlink()

    def bridge_pass(self):
        subprocess.run(['bash', str(BRIDGE), '--once'], env=self.env(), check=True,
                       capture_output=True, timeout=20)

    def option(self, pane, name):
        return self.tmux('display-message', '-p', '-t', pane, f'#{{{name}}}')

    # --- what the tab shows ----------------------------------------------------

    def test_a_blocked_agent_turns_the_tab_red(self):
        pane = self.pane('herdr')
        self.agents('default', 'idle', 'blocked', 'working')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'blocked')
        self.assertIn(BLOCK_RED, self.option(pane, '@cc_icon'))

    def test_a_working_agent_spins_the_tab(self):
        pane = self.pane('herdr')
        self.agents('default', 'idle', 'working', 'done')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'working')

    def test_done_stays_plain(self):
        pane = self.pane('herdr')
        self.agents('default', 'done', 'idle', 'unknown')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'idle')
        self.assertNotIn('#[fg=', self.option(pane, '@cc_icon'))

    def test_the_tab_follows_the_agents(self):
        pane = self.pane('herdr')
        self.agents('default', 'blocked')
        self.bridge_pass()
        self.agents('default', 'working')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'working')
        self.agents('default', 'done')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'idle')

    def test_a_status_spelled_in_a_title_is_not_a_status(self):
        pane = self.pane('herdr')
        self.agents('default', 'idle', title='"agent_status":"blocked"')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'idle')

    # --- which session a pane shows --------------------------------------------

    def test_each_pane_follows_its_own_session(self):
        plain = self.pane('herdr')
        named = self.pane('herdr', '--session', 'beaver')
        attached = self.pane('herdr', 'session', 'attach', 'gamma')
        self.agents('default', 'blocked')
        self.agents('beaver', 'working')
        self.agents('gamma', 'idle')
        self.bridge_pass()
        self.assertEqual(self.option(plain, '@cc_state'), 'blocked')
        self.assertEqual(self.option(named, '@cc_state'), 'working')
        self.assertEqual(self.option(attached, '@cc_state'), 'idle')
        self.assertEqual(self.option(named, '@herdr_session'), 'beaver')

    def test_a_remote_client_is_left_alone(self):
        pane = self.pane('herdr', '--remote', 'somehost')
        self.agents('default', 'blocked')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), '')

    def test_an_unreachable_server_leaves_the_tab_as_it_was(self):
        pane = self.pane('herdr')
        self.agents('default', 'blocked')
        self.bridge_pass()
        self.server_down('default')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'blocked')

    def test_a_pane_herdr_has_left_is_cleared(self):
        pane = self.pane('fish')
        self.tmux('set-option', '-p', '-t', pane, '@herdr_session', 'default')
        self.tmux('set-option', '-p', '-t', pane, '@cc_state', 'blocked')
        self.tmux('set-option', '-p', '-t', pane, '@cc_icon', 'x')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), '')
        self.assertEqual(self.option(pane, '@herdr_session'), '')

    def test_other_panes_are_left_alone(self):
        pane = self.pane('fish')
        self.tmux('set-option', '-p', '-t', pane, '@cc_state', 'idle')
        self.agents('default', 'blocked')
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'idle')

    # --- the shared ticker -------------------------------------------------------

    def test_the_ticker_keeps_a_working_herdr_pane(self):
        # tmux-status.sh --tick clears a "working" pane whose process is not an
        # assistant. herdr's pane has to count as one, or the spinner the
        # bridge started is wiped a second later.
        pane = self.pane('herdr')
        self.tmux('set-option', '-p', '-t', pane, '@cc_state', 'working')
        ticker = subprocess.Popen(['bash', str(STATUS), '--tick'], env=self.env(),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        time.sleep(1.5)
        os.killpg(ticker.pid, signal.SIGKILL)
        ticker.wait()
        self.assertEqual(self.option(pane, '@cc_state'), 'working')


if __name__ == '__main__':
    unittest.main()
