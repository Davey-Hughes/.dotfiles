#!/usr/bin/env python3
"""OpenClaw tmux status and restore, against a fake tmux; never the user's server.

The gateway plugin (config/openclaw/plugins/tmux-status/index.js) is driven
through node with the real renderer and the real footer parser, so this covers
the whole path from an OpenClaw hook to the pane options powerkit reads.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'config/openclaw/plugins/tmux-status'
SESSIONS = PLUGIN / 'tmux-session.sh'
RENDERER = ROOT / 'config/.claude/hooks/tmux-status.sh'

KEY_A = 'agent:main:tui-5b4f9fd2-ed98-4cfb-b6e4-ba394d7945d7'
KEY_B = 'agent:main:tui-0c1d2e3f-0000-4000-8000-000000000001'

FAKE_TMUX = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
p = Path(os.environ['TEST_STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
panes = s['panes']
def target():
    return a[a.index('-t') + 1] if '-t' in a else os.environ.get('TMUX_PANE', '')
def expand(fmt, pane):
    info = panes.get(pane, {})
    fixed = {'pane_id': pane, 'pane_current_command': info.get('cmd', ''),
             'session_name': info.get('session', 'main'), 'window_index': info.get('window', '0'),
             'pane_index': info.get('index', '0')}
    def sub(m):
        name = m.group(1)
        if name.startswith('@'):
            return info.get('opts', {}).get(name, '')
        return fixed.get(name, '')
    return re.sub(r'#\{([^}]*)\}', sub, fmt)
if a[0] == 'set-option':
    if '-g' in a:
        s.setdefault('global', {})[a[-2]] = a[-1]
        s['renames'] = s.get('renames', 0) + 1
    else:
        opts = panes.setdefault(target(), {}).setdefault('opts', {})
        if '-u' in a: opts.pop(a[-1], None)
        else: opts[a[-2]] = a[-1]
elif a[0] == 'show-options':
    print(s.get('global', {}).get(a[-1], 'on'))
elif a[0] == 'display-message':
    print(expand(a[-1], target()))
elif a[0] == 'list-panes':
    fmt = a[a.index('-F') + 1]
    for pane in panes:
        print(expand(fmt, pane))
elif a[0] == 'capture-pane':
    print(panes.get(target(), {}).get('screen', ''))
p.write_text(json.dumps(s))
'''

DRIVER = r'''
// node -e puts the first argument after `--` at argv[1].
const [plugin, renderer, sessions, stepsJson] = process.argv.slice(1);
const { createTmuxStatus } = await import(plugin);
const status = createTmuxStatus({ renderer, sessions, resolveTtlMs: 0, repeatWorkingMs: 0, startRetryMs: 10 });
const { readFileSync, writeFileSync } = await import("node:fs");
for (const step of JSON.parse(stepsJson)) {
  if (step.screen) {
    // The TUI in a pane redraws its footer, e.g. after /session.
    const state = JSON.parse(readFileSync(process.env.TEST_STATE, "utf8"));
    state.panes[step.screen.pane].screen = step.screen.text;
    writeFileSync(process.env.TEST_STATE, JSON.stringify(state));
    continue;
  }
  status.handlers[step.hook](step.event ?? {}, step.ctx ?? {});
  await status.idle();
}
'''


def footer(key, label=None, agent='main'):
    prefix = 'agent:' + agent + ':'
    rel = key[len(prefix):] if key.startswith(prefix) else key
    named = ' (' + label + ')' if label else ''
    return ('Some earlier reply text.\n connected | idle\n'
            ' agent ' + agent + ' (' + agent + ') | session ' + rel + named
            + ' | claude-opus-5-5 xhigh | deliver:off | tokens 76k/1.0m (8%)\n'
            + '\u2500' * 40 + '\n\n' + '\u2500' * 40)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.state = self.dir / 'state.json'
        (self.dir / 'tmux').write_text(FAKE_TMUX)
        (self.dir / 'tmux').chmod(0o755)
        # "lock held": the renderer never forks a spinner ticker here.
        (self.dir / 'flock').write_text('#!/bin/sh\nexit 1\n')
        (self.dir / 'flock').chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.dir) + ':' + os.environ['PATH'],
                    'TEST_STATE': str(self.state), 'XDG_RUNTIME_DIR': str(self.dir)}
        self.env.pop('TMUX_PANE', None)
        self.panes({})

    def panes(self, panes):
        self.state.write_text(json.dumps({'panes': panes}))

    def read(self):
        return json.loads(self.state.read_text())

    def opts(self, pane):
        return self.read()['panes'].get(pane, {}).get('opts', {})


class FooterTests(Base):
    def run_panes(self):
        out = subprocess.run(['bash', str(SESSIONS), 'panes'], env=self.env,
                             capture_output=True, text=True, check=True).stdout
        return [line.split('\t') for line in out.splitlines()]

    def test_reads_key_and_label(self):
        self.panes({'%1': {'cmd': 'openclaw', 'screen': footer(KEY_A, 'openxr')},
                    '%2': {'cmd': 'fish', 'screen': footer(KEY_B)},
                    '%3': {'cmd': 'openclaw', 'screen': footer(KEY_B)}})
        self.assertEqual(self.run_panes(), [['%1', KEY_A, 'openxr'], ['%3', KEY_B, '']])

    def test_label_may_hold_parentheses(self):
        self.panes({'%1': {'cmd': 'openclaw', 'screen': footer(KEY_A, 'fix (part 2)')}})
        self.assertEqual(self.run_panes(), [['%1', KEY_A, 'fix (part 2)']])

    def test_other_agent_and_qualified_key(self):
        self.panes({'%1': {'cmd': 'openclaw', 'screen': footer('agent:ops:main', agent='ops')},
                    '%2': {'cmd': 'openclaw', 'screen': footer('agent:ops:main', agent='main')}})
        self.assertEqual(self.run_panes(), [['%1', 'agent:ops:main', ''], ['%2', 'agent:ops:main', '']])

    def test_last_footer_on_screen_wins(self):
        quoted = footer(KEY_B) + '\n'
        self.panes({'%1': {'cmd': 'openclaw', 'screen': quoted + footer(KEY_A)}})
        self.assertEqual(self.run_panes(), [['%1', KEY_A, '']])

    def test_fails_closed(self):
        truncated = footer(KEY_A).replace('7945d7', '79\u2026')
        self.panes({'%1': {'cmd': 'openclaw', 'screen': truncated},
                    '%2': {'cmd': 'openclaw', 'screen': footer('agent:main:x;id')},
                    '%3': {'cmd': 'openclaw', 'screen': footer('agent:main:$(id)')},
                    '%4': {'cmd': 'openclaw', 'screen': 'no footer here'}})
        self.assertEqual(self.run_panes(), [])


class SaveTests(Base):
    def test_rewrites_placeable_panes_only(self):
        self.panes({'%1': {'cmd': 'openclaw', 'session': 'main', 'window': '5', 'index': '1',
                           'screen': footer(KEY_A)},
                    '%2': {'cmd': 'openclaw', 'session': 'main', 'window': '6', 'index': '0',
                           'screen': 'still starting'},
                    '%3': {'cmd': 'claude', 'session': 'main', 'window': '7', 'index': '0',
                           'screen': footer(KEY_B)}})

        def line(window, index, cmd, full):
            return '\t'.join(['pane', 'main', window, '1', ':*', index, 'title', ':/home/d', '1', cmd, full])
        save = self.dir / 'save.txt'
        rows = [line('5', '1', 'openclaw', ':openclaw'), line('6', '0', 'openclaw', ':openclaw'),
                line('7', '0', 'claude', ':claude'), 'window\tmain\t5\t:x\t1\t:*\tlayout\t:']
        save.write_text('\n'.join(rows) + '\n')
        subprocess.run(['bash', str(SESSIONS), '--save', str(save)], env=self.env, check=True)
        got = [r.split('\t') for r in save.read_text().splitlines()]
        self.assertEqual(got[0][10], ':openclaw tui --session ' + KEY_A)
        self.assertEqual(got[1][10], ':openclaw')
        self.assertEqual(got[2][10], ':claude')
        self.assertEqual(len(got), len(rows))
        self.assertEqual(list(self.dir.glob('save.txt.openclaw.*')), [])


class PluginTests(Base):
    def setUp(self):
        super().setUp()
        self.node = shutil.which('node')
        if not self.node:
            self.skipTest('node is not installed')
        self.panes({'%1': {'cmd': 'openclaw', 'screen': footer(KEY_A, 'openxr')},
                    '%2': {'cmd': 'openclaw', 'screen': footer(KEY_B)},
                    '%3': {'cmd': 'claude', 'screen': footer(KEY_A)}})

    def drive(self, *steps):
        result = subprocess.run([self.node, '--input-type=module', '-e', DRIVER, '--',
                                 str(PLUGIN / 'index.js'), str(RENDERER), str(SESSIONS),
                                 json.dumps(list(steps))],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def hook(name, key=KEY_A, event=None, **ctx):
        return {'hook': name, 'event': event or {}, 'ctx': {'sessionKey': key, **ctx}}

    def end(self, text, key=KEY_A):
        return self.hook('agent_end', key, {'success': True, 'messages': [
            {'role': 'user', 'content': 'hi'},
            {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]},
            {'role': 'custom', 'content': 'yield marker'}]})

    def test_turn_paints_only_panes_showing_the_session(self):
        self.drive(self.hook('model_call_started'))
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')
        self.assertNotIn('@cc_state', self.opts('%2'))
        self.assertNotIn('@cc_state', self.opts('%3'))
        self.drive(self.end('Done.'))
        self.assertEqual(self.opts('%1')['@cc_state'], 'idle')

    def test_dialogs_block_until_answered(self):
        ask = {'toolName': 'ask_user', 'params': {}}
        self.drive(self.hook('model_call_started'), self.hook('before_tool_call', event=ask))
        self.assertEqual(self.opts('%1')['@cc_state'], 'blocked')
        self.drive(self.hook('after_tool_call', event=ask))
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')
        self.drive(self.hook('before_tool_call',
                             event={'toolName': 'secrets', 'params': {'action': 'request'}}))
        self.assertEqual(self.opts('%1')['@cc_state'], 'blocked')
        self.drive(self.hook('before_tool_call', event={'toolName': 'exec', 'params': {}}))
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')

    def test_question_failure_and_abort(self):
        self.drive(self.hook('model_call_started'), self.end('**Which one?**'))
        self.assertEqual(self.opts('%1')['@cc_state'], 'asked')
        self.drive(self.hook('model_call_started'),
                   self.hook('agent_end', event={'success': False, 'error': 'rate_limit', 'messages': []}))
        self.assertEqual(self.opts('%1')['@cc_state'], 'blocked')
        self.drive(self.hook('model_call_started'),
                   self.hook('agent_end', event={'success': False, 'error': 'Run aborted', 'messages': []}))
        self.assertEqual(self.opts('%1')['@cc_state'], 'idle')

    def test_background_subagents_keep_the_tab_busy(self):
        child = 'agent:main:subagent:1'
        link = {'requesterSessionKey': KEY_A, 'childSessionKey': child}
        spawned = {'hook': 'subagent_spawned', 'event': {'childSessionKey': child}, 'ctx': link}
        ended = {'hook': 'subagent_ended', 'event': {'targetSessionKey': child}, 'ctx': link}
        # Spawned twice on purpose: the count is of distinct children.
        self.drive(self.hook('model_call_started'), spawned, spawned, self.end('Started it.'))
        self.assertEqual(self.opts('%1')['@cc_subs'], '1')
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')
        self.drive(spawned, ended)
        self.assertEqual(self.opts('%1')['@cc_subs'], '0')
        self.assertEqual(self.opts('%1')['@cc_state'], 'idle')

    def test_label_names_the_window(self):
        self.drive(self.hook('model_call_started'))
        self.assertEqual(self.opts('%1')['@cc_name'], 'openxr')
        self.assertNotIn('@cc_name', self.opts('%2'))
        self.assertGreater(self.read().get('renames', 0), 0)
        self.drive(self.hook('session_start'))
        self.assertEqual(self.opts('%1')['@cc_name'], 'openxr')

    def test_switching_session_resets_the_tab(self):
        child = 'agent:main:subagent:2'
        link = {'requesterSessionKey': KEY_B, 'childSessionKey': child}
        # %1 is mid-turn on KEY_A when its TUI switches to KEY_B, which has a
        # background child running. Any later event, for any session, notices.
        self.drive(self.hook('model_call_started'), self.end('**Which one?**'),
                   {'hook': 'subagent_spawned', 'event': {}, 'ctx': link},
                   {'screen': {'pane': '%1', 'text': footer(KEY_B, 'other')}},
                   self.hook('model_call_started', 'agent:main:elsewhere'))
        self.assertEqual(self.opts('%1')['@cc_name'], 'other')
        self.assertEqual(self.opts('%1')['@cc_subs'], '1')
        self.assertNotIn('@cc_asked', self.opts('%1'))
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')
        self.assertEqual(self.opts('%1')['@cc_colour'], '#bb9af7')

    def test_session_end_clears(self):
        self.drive(self.hook('model_call_started'), self.end('Done.'),
                   {'hook': 'session_end', 'event': {'sessionKey': KEY_A, 'reason': 'reset'}})
        self.assertNotIn('@cc_state', self.opts('%1'))
        self.assertNotIn('@cc_name', self.opts('%1'))

    def test_compaction_keeps_state(self):
        self.drive(self.hook('model_call_started'),
                   {'hook': 'session_end', 'event': {'sessionKey': KEY_A, 'reason': 'compaction'}})
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')

    def test_no_tmux_is_harmless(self):
        bin_dir = self.dir / 'no-tmux'
        bin_dir.mkdir()
        (bin_dir / 'bash').symlink_to(shutil.which('bash'))
        self.env['PATH'] = str(bin_dir)
        self.drive(self.hook('model_call_started'), self.end('Done.'))


if __name__ == '__main__':
    unittest.main()
