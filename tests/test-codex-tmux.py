#!/usr/bin/env python3
"""Test status hooks with a fake tmux; never touch the user's server."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SESSION = '01a12ec4-8696-7190-87c2-a6eb78dbe783'
OTHER = '01a129e6-5ce7-7f32-a941-094e77a252c1'
CHILD = '01a12f25-5559-78a0-97fd-d731888a20af'

def shown(thread, name='chat'):
    """The pane title Codex writes for tui.terminal_title = ["thread", "thread-id"]."""
    return f'{name} | {thread[:29]}...'

class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.state = self.directory / 'state.json'
        # %12 is where the app-server daemon was started, and so the TMUX_PANE
        # every hook inherits. It shows a different conversation.
        self.state.write_text(json.dumps({'panes': {
            '%7': {'pane_title': shown(SESSION), 'pane_current_command': 'codex'},
            '%12': {'pane_title': shown(OTHER), 'pane_current_command': 'codex'}}}))
        tmux = self.directory / 'tmux'
        tmux.write_text(r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
p = Path(os.environ['TEST_STATE'])
s = json.loads(p.read_text())
panes = s['panes']
a = sys.argv[1:]
target = a[a.index('-t') + 1] if '-t' in a else None
def expand(fmt, pane):
    values = {'socket_path': '/fake', 'session_name': 'test', 'window_index': '0', 'pane_index': '0'}
    if pane: values.update(panes[pane], pane_id=pane)
    return re.sub(r'#\{([^{}]*)\}', lambda m: values.get(m.group(1), ''), fmt)
if a[0] == 'set-option' and '-p' in a:
    if target not in panes: sys.exit(1)
    if '-u' in a: panes[target].pop(a[-1], None)
    else: panes[target][a[-2]] = a[-1]
elif a[0] == 'display-message' and '-p' in a:
    if target and target not in panes: sys.exit(1)
    print(expand(a[-1], target))
elif a[0] == 'list-panes':
    for pane in panes: print(expand(a[-1], pane))
elif a[0] == 'select-pane': s['pane'] = a[-1]
elif a[0] == 'switch-client': s['selected'] = a[-1]
p.write_text(json.dumps(s))
''')
        tmux.chmod(0o755)
        flock = self.directory / 'flock'
        flock.write_text('#!/bin/sh\nexit 1\n')
        flock.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.directory) + ':' + os.environ['PATH'],
                    'TEST_STATE': str(self.state), 'TMUX_PANE': '%12',
                    'CODEX_HOME': str(ROOT / 'config/codex'),
                    'XDG_CONFIG_HOME': str(ROOT / 'config'),
                    'XDG_STATE_HOME': str(self.directory / 'state'),
                    'XDG_RUNTIME_DIR': str(self.directory)}

    def panes(self):
        return json.loads(self.state.read_text())['panes']

    def change(self, pane, **values):
        state = json.loads(self.state.read_text())
        state['panes'][pane].update(values)
        self.state.write_text(json.dumps(state))

    def hook(self, event, **payload):
        """Fire one hook for SESSION and return the activity %7 then shows."""
        config = json.loads((ROOT / 'config/codex/hooks.json').read_text())
        groups = config['hooks'].get(event, [])
        commands = [h['command'] for g in groups for h in g['hooks']
                    if 'tmux-status' in h['command']]
        self.assertTrue(commands, event)
        for command in commands:
            result = subprocess.run(['sh', '-c', command], env=self.env,
                                    input=json.dumps({'hook_event_name': event,
                                                      'session_id': SESSION, **payload}),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
        return self.panes()['%7'].get('@cc_state')

    def test_next_selects_waiting_codex_pane(self):
        self.change('%7', **{'@cc_state': 'idle', 'pane_current_command': 'claude'})
        self.change('%12', **{'@cc_state': 'blocked'})
        result = subprocess.run(['bash', str(ROOT / 'config/.claude/hooks/tmux-status.sh'), 'next'],
                                env={**self.env, 'TMUX_PANE': '%1'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state.read_text())['pane'], '%12')

    def test_turn_lifecycle(self):
        self.assertEqual(self.hook('SessionStart'), 'idle')
        self.assertEqual(self.hook('UserPromptSubmit'), 'working')
        self.assertEqual(self.hook('PermissionRequest'), 'blocked')
        self.assertEqual(self.hook('PostToolUse'), 'working')
        self.assertEqual(self.hook('Stop', last_assistant_message='Done.'), 'idle')
        self.assertIsNone(self.hook('SessionEnd'))

    def test_daemon_pane_is_left_alone(self):
        self.assertEqual(self.hook('UserPromptSubmit'), 'working')
        self.assertNotIn('@cc_state', self.panes()['%12'])

    def test_every_pane_showing_the_session(self):
        self.change('%12', pane_title=shown(SESSION))
        self.assertEqual(self.hook('UserPromptSubmit'), 'working')
        self.assertEqual(self.panes()['%12'].get('@cc_state'), 'working')

    def test_session_on_no_pane_changes_nothing(self):
        self.change('%7', pane_title='chat')
        self.assertIsNone(self.hook('UserPromptSubmit'))
        self.assertNotIn('@cc_state', self.panes()['%12'])

    def test_event_without_session_id_changes_nothing(self):
        self.assertIsNone(self.hook('UserPromptSubmit', session_id=''))
        self.assertNotIn('@cc_state', self.panes()['%12'])

    def test_pane_that_left_codex_changes_nothing(self):
        self.change('%7', pane_current_command='fish')
        self.assertIsNone(self.hook('UserPromptSubmit'))

    def test_subagent_thread_on_screen(self):
        self.change('%7', pane_title=shown(CHILD, 'worker'))
        self.assertEqual(self.hook('PreToolUse', agent_id=CHILD), 'working')

    def test_root_events_land_while_its_subagent_is_on_screen(self):
        self.hook('UserPromptSubmit')
        self.hook('SubagentStart', agent_id=CHILD)
        self.change('%7', pane_title=shown(CHILD, 'worker'))
        self.assertEqual(self.hook('Stop', last_assistant_message='Done.'), 'working')
        self.assertEqual(self.hook('SubagentStop', agent_id=CHILD), 'idle')

    def test_unrelated_thread_on_screen_changes_nothing(self):
        self.hook('UserPromptSubmit')
        self.change('%7', pane_title=shown(OTHER))
        self.assertEqual(self.hook('Stop', last_assistant_message='Done.'), 'working')

    @unittest.skipUnless(Path('/proc/self/stat').exists(), 'needs Linux /proc')
    def test_registers_the_pane_for_resurrect(self):
        # A stand-in TUI: a process named codex below the pane's shell, here this test.
        codex = self.directory / 'codex'
        shutil.copyfile(shutil.which('sleep'), codex)
        codex.chmod(0o755)
        tui = subprocess.Popen([str(codex), '30'])
        self.addCleanup(tui.wait)
        self.addCleanup(tui.kill)
        self.change('%7', pane_pid=str(os.getpid()))
        self.change('%12', pane_pid='1')
        # The daemon need not have started inside tmux at all.
        self.env.pop('TMUX', None)
        self.hook('UserPromptSubmit', transcript_path=str(self.state))
        records = {path.name: json.loads(path.read_text()) for path in
                   (self.directory / 'state').glob('codex/tmux-sessions/*/*.json')}
        self.assertEqual(list(records), ['7.json'])
        self.assertEqual((records['7.json']['pid'], records['7.json']['session_id']),
                         (tui.pid, SESSION))

    def test_question_and_interrupt(self):
        self.hook('UserPromptSubmit')
        self.assertEqual(self.hook('Stop', last_assistant_message='Which one?'), 'asked')
        self.hook('UserPromptSubmit')
        self.assertEqual(self.hook('Interrupt'), 'idle')

    def test_compaction_preserves_activity(self):
        self.hook('UserPromptSubmit')
        self.assertEqual(self.hook('SessionStart', source='compact'), 'working')

    def test_input_dialog(self):
        self.assertEqual(self.hook('PreToolUse', tool_name='request_user_input'), 'blocked')

    def test_subagent_start_does_not_reset_main(self):
        self.hook('UserPromptSubmit')
        self.hook('SubagentStart')
        self.hook('SessionStart', agent_id='child')
        self.assertEqual(self.hook('Stop', last_assistant_message='Done.'), 'working')
        self.assertEqual(self.hook('SubagentStop'), 'idle')

if __name__ == '__main__':
    unittest.main()
