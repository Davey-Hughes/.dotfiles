#!/usr/bin/env python3
"""Test status hooks with a fake tmux; never touch the user's server."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.state = self.directory / 'state.json'
        self.state.write_text('{}')
        tmux = self.directory / 'tmux'
        tmux.write_text(r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['TEST_STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
if a[0] == 'set-option':
    if '-u' in a: s.pop(a[-1], None)
    else: s[a[-2]] = a[-1]
elif a[0] == 'display-message':
    fmt = a[-1]
    if fmt == '#{session_name} #{window_index}': fmt = 'test 0'
    for key, value in s.items(): fmt = fmt.replace('#{' + key + '}', value)
    import re
    print(re.sub(r'#\{[^}]*\}', '', fmt))
elif a[0] == 'list-panes':
    print('%2 blocked codex')
    print('%3 idle claude')
elif a[0] == 'select-pane': s['pane'] = a[-1]
elif a[0] == 'switch-client': s['selected'] = a[-1]
p.write_text(json.dumps(s))
''')
        tmux.chmod(0o755)
        flock = self.directory / 'flock'
        flock.write_text('#!/bin/sh\nexit 1\n')
        flock.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.directory) + ':' + os.environ['PATH'],
                    'TEST_STATE': str(self.state), 'TMUX_PANE': '%1',
                    'CODEX_HOME': str(ROOT / 'config/codex'),
                    'XDG_CONFIG_HOME': str(ROOT / 'config'),
                    'XDG_RUNTIME_DIR': str(self.directory)}

    def hook(self, event, **payload):
        config = json.loads((ROOT / 'config/codex/hooks.json').read_text())
        groups = config['hooks'].get(event, [])
        commands = [h['command'] for g in groups for h in g['hooks']
                    if 'tmux-status' in h['command']]
        self.assertTrue(commands, event)
        for command in commands:
            result = subprocess.run(['sh', '-c', command], env=self.env,
                                    input=json.dumps({'hook_event_name': event, **payload}),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
        return json.loads(self.state.read_text())

    def test_next_selects_waiting_codex_pane(self):
        result = subprocess.run(['bash', str(ROOT / 'config/.claude/hooks/tmux-status.sh'), 'next'],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state.read_text())['pane'], '%2')

    def test_turn_lifecycle(self):
        self.assertEqual(self.hook('SessionStart')['@cc_state'], 'idle')
        self.assertEqual(self.hook('UserPromptSubmit')['@cc_state'], 'working')
        self.assertEqual(self.hook('PermissionRequest')['@cc_state'], 'blocked')
        self.assertEqual(self.hook('PostToolUse')['@cc_state'], 'working')
        self.assertEqual(self.hook('Stop', last_assistant_message='Done.')['@cc_state'], 'idle')
        self.assertNotIn('@cc_state', self.hook('SessionEnd'))

    def test_question_and_interrupt(self):
        self.hook('UserPromptSubmit')
        self.assertEqual(self.hook('Stop', last_assistant_message='Which one?')['@cc_state'], 'asked')
        self.hook('UserPromptSubmit')
        self.assertEqual(self.hook('Interrupt')['@cc_state'], 'idle')

    def test_compaction_preserves_activity(self):
        self.hook('UserPromptSubmit')
        self.assertEqual(self.hook('SessionStart', source='compact')['@cc_state'], 'working')

    def test_input_dialog(self):
        self.assertEqual(self.hook('PreToolUse', tool_name='request_user_input')['@cc_state'], 'blocked')

    def test_subagent_start_does_not_reset_main(self):
        self.hook('UserPromptSubmit')
        self.hook('SubagentStart')
        self.hook('SessionStart', agent_id='child')
        self.assertEqual(self.hook('Stop', last_assistant_message='Done.')['@cc_state'], 'working')
        self.assertEqual(self.hook('SubagentStop')['@cc_state'], 'idle')

if __name__ == '__main__':
    unittest.main()
