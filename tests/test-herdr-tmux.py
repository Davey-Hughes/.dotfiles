#!/usr/bin/env python3
"""herdr's agent states on the tmux tab herdr runs in, on an isolated tmux server.

home/.tmux/herdr-status.sh asks each herdr session for its agents and hands the
most urgent state to tmux-status.sh for the tmux pane showing that session. On
the same pass it renames herdr's tabs after their agents' sessions. These run
one pass of it against a private tmux server, with a stand-in herdr client in
the pane and a stand-in `herdr` CLI on PATH that prints canned agent and tab
lists and logs every change asked of it, so neither the user's tmux nor their
herdr server is touched.
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
# What Codex puts after the chat name in its title: the thread id, cut to 32
# characters with the last three an ellipsis (see templates/codex/config.toml).
THREAD = ' | 01a12f98-2517-7910-a451-6c0f8...'
REPORT = ['pane', 'report-metadata', 'w1:p1', '--source', 'dotfiles:herdr-status']
# What marks a Claude sidebar name whose turn ended on a question: a zero-width
# space, which herdr keeps and a sidebar rule turns red, but nobody sees.
MARK = chr(0x200B)

# Prints $HERDR_STUB/<session>.json for `herdr [--session NAME] agent list`
# and <session>.tabs.json for `tab list`, and fails when there is no such
# file, the way an unreachable server does. Tab renames and metadata reports
# are appended to calls.jsonl, one JSON array of [session, *args] per call.
CLI_STUB = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
stub = Path(os.environ['HERDR_STUB'])
args = sys.argv[1:]
session = 'default'
if args[:1] == ['--session']:
    session, args = args[1], args[2:]
def show(name):
    if not (stub / name).is_file():
        sys.exit(1)
    sys.stdout.write((stub / name).read_text())
if args == ['agent', 'list']:
    show(f'{session}.json')
elif args == ['tab', 'list']:
    show(f'{session}.tabs.json')
elif args[:2] in (['tab', 'rename'], ['pane', 'report-metadata']):
    with open(stub / 'calls.jsonl', 'a') as log:
        log.write(json.dumps([session, *args]) + chr(10))
else:
    sys.exit(2)
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

    def listing(self, session, *agents, tabs=()):
        """Agents as `herdr agent list` gives them, and `tabs` as (id, label)."""
        agents = [{'agent_status': 'idle', 'pane_id': f'w1:p{i}', 'tab_id': 'w1:t1', **a}
                  for i, a in enumerate(agents, 1)]
        compact = {'separators': (',', ':')}
        (self.stub / f'{session}.json').write_text(json.dumps(
            {'id': 'cli:agent:list', 'result': {'agents': agents, 'type': 'agent_list'}},
            **compact))
        (self.stub / f'{session}.tabs.json').write_text(json.dumps(
            {'id': 'cli:tab:list', 'result': {'tabs': [
                {'tab_id': tab, 'label': label, 'workspace_id': tab.split(':')[0]}
                for tab, label in tabs], 'type': 'tab_list'}}, **compact))

    def calls(self, kind=None):
        """What was asked of herdr; `kind` keeps only `tab` renames or `pane` reports."""
        log = self.stub / 'calls.jsonl'
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [call for call in calls if kind is None or call[1] == kind]

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

    # --- a turn that ended on a question (the $asked token) ----------------------

    def asked(self, status):
        return {'agent': 'claude', 'agent_status': status, 'tokens': {'asked': 'asked'}}

    def test_a_turn_that_ended_on_a_question_turns_the_tab_red(self):
        pane = self.pane('herdr')
        self.listing('default', self.asked('done'))
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'blocked')
        self.assertIn(BLOCK_RED, self.option(pane, '@cc_icon'))

    def test_a_question_outranks_a_working_agent(self):
        pane = self.pane('herdr')
        self.listing('default', {'agent': 'claude', 'agent_status': 'working'},
                     self.asked('idle'))
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'blocked')

    def test_a_question_already_answered_does_not_redden_a_working_agent(self):
        pane = self.pane('herdr')
        self.listing('default', self.asked('working'))
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'working')

    def test_a_token_left_behind_for_another_agent_is_ignored(self):
        # Pane metadata outlives the agent: a Claude killed with $asked set
        # leaves it on the pane for whatever runs there next.
        pane = self.pane('herdr')
        self.listing('default', {**self.asked('idle'), 'agent': 'codex'})
        self.bridge_pass()
        self.assertEqual(self.option(pane, '@cc_state'), 'idle')

    def test_asked_spelled_in_a_title_is_not_a_question(self):
        pane = self.pane('herdr')
        self.listing('default', {'agent': 'claude', 'agent_status': 'done',
                                 'terminal_title': '"asked":"asked"'})
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

    # --- herdr's tab names ------------------------------------------------------

    @staticmethod
    def claude(name, **extra):
        return {'agent': 'claude', 'terminal_title': f'* {name}',
                'terminal_title_stripped': name, **extra}

    @staticmethod
    def codex(title, **extra):
        return {'agent': 'codex', 'terminal_title': title,
                'terminal_title_stripped': title, **extra}

    def test_a_tab_takes_its_claude_session_name(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink'), tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls('tab'), [['default', 'tab', 'rename', 'w1:t1', 'zink']])

    def test_a_tab_already_named_is_left_alone(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink'), tabs=[('w1:t1', 'zink')])
        self.bridge_pass()
        self.assertEqual(self.calls('tab'), [])

    def test_a_tab_renamed_by_hand_follows_the_session_again(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink'), tabs=[('w1:t1', 'my name')])
        self.bridge_pass()
        self.assertEqual(self.calls('tab'), [['default', 'tab', 'rename', 'w1:t1', 'zink']])

    def test_a_tab_without_a_named_agent_keeps_its_label(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink'), {'agent': 'opencode', 'tab_id': 'w1:t2'},
                     tabs=[('w1:t1', 'zink'), ('w1:t2', 'scratch'), ('w1:t3', 'shell')])
        self.bridge_pass()
        self.assertEqual(self.calls('tab'), [])

    def test_the_first_named_agent_names_a_split_tab(self):
        self.pane('herdr')
        self.listing('default', {'agent': 'opencode'}, self.claude('zink'),
                     self.claude('melonds'), tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls('tab'), [['default', 'tab', 'rename', 'w1:t1', 'zink']])

    def test_a_name_is_one_line(self):
        self.pane('herdr')
        self.listing('default', self.claude(' two\nlines\t'), tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls('tab'), [['default', 'tab', 'rename', 'w1:t1', 'two lines']])

    def test_opencode_names_its_tab_with_the_title_it_reports(self):
        # The title, not the terminal title, which OpenCode prefixes and cuts.
        self.pane('herdr')
        self.listing('default', {'agent': 'opencode',
                                 'terminal_title_stripped': 'OC | Eden libretro core p...',
                                 'tokens': {'session': 'Eden libretro core port'}},
                     tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls(),
                         [['default', 'tab', 'rename', 'w1:t1', 'Eden libretro core port']])

    def test_codex_drops_the_thread_id_and_reports_its_name(self):
        self.pane('herdr')
        self.listing('default', self.codex('Port Codex config' + THREAD), tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls(), [
            ['default', *REPORT, '--token', 'session=Port Codex config'],
            ['default', 'tab', 'rename', 'w1:t1', 'Port Codex config']])

    def test_a_codex_name_already_reported_is_not_sent_again(self):
        self.pane('herdr')
        self.listing('default', self.codex('Port Codex config' + THREAD,
                                           tokens={'session': 'Port Codex config'}),
                     tabs=[('w1:t1', 'Port Codex config')])
        self.bridge_pass()
        self.assertEqual(self.calls(), [])

    def test_codex_with_only_a_thread_id_has_no_name(self):
        self.pane('herdr')
        self.listing('default', self.codex(THREAD[3:]), tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls(), [])

    def test_a_codex_name_that_went_away_is_cleared(self):
        self.pane('herdr')
        self.listing('default', self.codex(THREAD[3:], tokens={'session': 'old chat'}),
                     tabs=[('w1:t1', 'old chat')])
        self.bridge_pass()
        self.assertEqual(self.calls(), [['default', *REPORT, '--clear-token', 'session']])

    def test_a_named_session_is_renamed_through_its_own_server(self):
        self.pane('herdr', '--session', 'beaver')
        self.listing('beaver', self.claude('zink'), tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls('tab'), [['beaver', 'tab', 'rename', 'w1:t1', 'zink']])

    # --- herdr's sidebar name for Claude ($session) ------------------------------

    def test_a_claude_name_is_reported_for_the_sidebar(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink'))
        self.bridge_pass()
        self.assertEqual(self.calls('pane'), [['default', *REPORT, '--token', 'session=zink']])

    def test_a_question_marks_the_sidebar_name_but_not_the_tab(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink', agent_status='done',
                                            tokens={'asked': 'asked', 'session': 'zink'}),
                     tabs=[('w1:t1', '1')])
        self.bridge_pass()
        self.assertEqual(self.calls('pane'), [['default', *REPORT, '--token', 'session=' + MARK + 'zink']])
        self.assertEqual(self.calls('tab'), [['default', 'tab', 'rename', 'w1:t1', 'zink']])

    def test_an_answered_question_unmarks_the_sidebar_name(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink', agent_status='working',
                                            tokens={'asked': 'asked', 'session': MARK + 'zink'}))
        self.bridge_pass()
        self.assertEqual(self.calls('pane'), [['default', *REPORT, '--token', 'session=zink']])

    def test_a_sidebar_name_already_reported_is_not_sent_again(self):
        self.pane('herdr')
        self.listing('default', self.claude('zink', tokens={'session': 'zink'}),
                     self.claude('melonds', agent_status='idle',
                                 tokens={'asked': 'asked', 'session': MARK + 'melonds'}))
        self.bridge_pass()
        self.assertEqual(self.calls('pane'), [])

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


@unittest.skipUnless(shutil.which('bash') and shutil.which('jq') and shutil.which('tmux'),
                     'needs bash, jq and tmux')
class HookInsideHerdrTests(unittest.TestCase):
    """Claude's hooks inside herdr report a turn that ended on a question.

    herdr starts its agents without TMUX_PANE, so tmux-status.sh has no tmux
    pane to paint there. Instead it reports the pane token $asked to herdr,
    which herdr-status.sh reads and herdr's sidebar shows.
    """
    REPORT = ['default', 'pane', 'report-metadata', 'w1:p1', '--source', 'dotfiles:tmux-status']
    QUESTION = 'Tests pass.\n\n**Which approach do you want?**'

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='herdr-hook-'))
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        cli = self.bin / 'herdr'
        cli.write_text(CLI_STUB)
        cli.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def hook(self, verb, event, **env_extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ('TMUX', 'TMUX_PANE')
               and not k.startswith(('HERDR_', 'TMUX_STATUS_'))}
        env.update(PATH=f'{self.bin}:{env.get("PATH", "/usr/bin:/bin")}',
                   HERDR_STUB=str(self.dir), HERDR_ENV='1', HERDR_PANE_ID='w1:p1',
                   XDG_RUNTIME_DIR=str(self.dir), **env_extra)
        result = subprocess.run(['bash', str(STATUS), verb], input=json.dumps(event),
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def stop(self, message, **env_extra):
        self.hook('idle', {'hook_event_name': 'Stop', 'last_assistant_message': message},
                  **env_extra)

    def calls(self):
        log = self.dir / 'calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_a_turn_that_ends_on_a_question_reports_asked(self):
        self.stop(self.QUESTION)
        self.assertEqual(self.calls(), [self.REPORT + ['--token', 'asked=asked']])

    def test_a_turn_that_ends_on_a_statement_clears_asked(self):
        self.stop('All tests pass.')
        self.assertEqual(self.calls(), [self.REPORT + ['--clear-token', 'asked']])

    def test_moving_on_clears_asked(self):
        self.hook('working', {'hook_event_name': 'UserPromptSubmit'})
        self.hook('working', {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash'})
        self.hook('reset', {'hook_event_name': 'SessionStart'})
        self.hook('off', {'hook_event_name': 'SessionEnd'})
        self.assertEqual(self.calls(), [self.REPORT + ['--clear-token', 'asked']] * 4)

    def test_dialogs_are_left_to_herdr(self):
        self.hook('blocked', {'hook_event_name': 'PermissionRequest'})
        self.assertEqual(self.calls(), [])

    def test_other_assistants_do_not_report(self):
        # Codex runs its hooks in one daemon whose HERDR_PANE_ID may be any
        # pane's; OpenCode and OpenClaw paint tmux through their own adapters.
        for adapter in ('TMUX_STATUS_CODEX', 'TMUX_STATUS_OPENCODE', 'TMUX_STATUS_OPENCLAW'):
            self.stop(self.QUESTION, **{adapter: '1'})
        self.assertEqual(self.calls(), [])

    def test_verbs_tmux_runs_do_not_report(self):
        # A tmux server started inside herdr hands HERDR_PANE_ID to its jobs.
        for verb in ('next', 'name', 'names'):
            self.hook(verb, {}, TMUX=f'{self.dir}/no-server,0,0')
        self.assertEqual(self.calls(), [])

    def test_a_hook_in_a_tmux_pane_leaves_herdr_alone(self):
        self.stop(self.QUESTION, TMUX_PANE='%0', TMUX=f'{self.dir}/no-server,0,0')
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
