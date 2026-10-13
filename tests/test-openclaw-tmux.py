#!/usr/bin/env python3
"""OpenClaw tmux status and restore, against a fake tmux; never the user's server.

The bridge (config/openclaw/tmux-status/bridge.js) is driven through node with
Gateway events shaped like the ones a live Gateway sends, the real renderer and
the real footer parser, so this covers the whole path from a Gateway event to
the pane options powerkit reads. The connection itself is not exercised here.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = ROOT / 'config/openclaw/tmux-status'
BRIDGE = BRIDGE_DIR / 'bridge.js'
SESSIONS = BRIDGE_DIR / 'tmux-session.sh'
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
const [bridge, renderer, sessions, stepsJson] = process.argv.slice(1);
const { createTmuxStatus } = await import(bridge);
const status = createTmuxStatus({ renderer, sessions, resolveTtlMs: 0, repeatWorkingMs: 0, startRetryMs: 10 });
const { readFileSync, writeFileSync } = await import("node:fs");
const state = () => JSON.parse(readFileSync(process.env.TEST_STATE, "utf8"));
const snaps = [];
for (const step of JSON.parse(stepsJson)) {
  if (step.screen) {
    // The TUI in a pane redraws its footer, e.g. after /session.
    const s = state();
    s.panes[step.screen.pane].screen = step.screen.text;
    writeFileSync(process.env.TEST_STATE, JSON.stringify(s));
    continue;
  }
  if (step.snap) {
    const s = state();
    snaps.push(Object.fromEntries(Object.entries(s.panes).map(([p, v]) => [p, v.opts ?? {}])));
    continue;
  }
  if (step.refresh) status.refresh(true);
  else if (step.seed) status.seed(step.seed);
  else status.handle(step.event, step.payload);
  await status.idle();
}
await status.idle();
process.stdout.write(JSON.stringify({ snaps, shown: [...status.shownKeys()] }));
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


def changed(key=KEY_A, running=None, **row):
    """A sessions.changed delta. running=None leaves the run state out."""
    payload = {'sessionKey': key, **row}
    if running is not None:
        payload['hasActiveRun'] = running
        payload.setdefault('status', 'running' if running else 'done')
    return {'event': 'sessions.changed', 'payload': payload}


def start(key=KEY_A, **row):
    return changed(key, True, phase='start', **row)


def settle(key=KEY_A, status='done', **row):
    return changed(key, False, reason='agent.input.settled', status=status, **row)


def reply(text, key=KEY_A):
    return {'event': 'session.message', 'payload': {'sessionKey': key, 'message': {
        'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'hm'},
                                         {'type': 'text', 'text': text}]}}}


def tool(name, phase='start', key=KEY_A, args=None):
    data = {'phase': phase, 'name': name, 'toolCallId': 'call-' + name}
    if args is not None:
        data['args'] = args
    return {'event': 'session.tool', 'payload': {'sessionKey': key, 'stream': 'tool', 'data': data}}


def end(text, key=KEY_A):
    return [reply(text, key), settle(key)]


SNAP = {'snap': True}


class BridgeTests(Base):
    def setUp(self):
        super().setUp()
        self.node = shutil.which('node')
        if not self.node:
            self.skipTest('node is not installed')
        self.panes({'%1': {'cmd': 'openclaw', 'screen': footer(KEY_A, 'openxr')},
                    '%2': {'cmd': 'openclaw', 'screen': footer(KEY_B)},
                    '%3': {'cmd': 'claude', 'screen': footer(KEY_A)}})

    def drive(self, *steps):
        flat = []
        for step in steps:
            flat.extend(step if isinstance(step, list) else [step])
        result = subprocess.run([self.node, '--input-type=module', '-e', DRIVER, '--',
                                 str(BRIDGE), str(RENDERER), str(SESSIONS), json.dumps(flat)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_turn_paints_only_panes_showing_the_session(self):
        snaps = self.drive(start(), SNAP, end('Done.'))['snaps']
        self.assertEqual(snaps[0]['%1']['@cc_state'], 'working')
        self.assertNotIn('@cc_state', snaps[0]['%2'])
        self.assertNotIn('@cc_state', snaps[0].get('%3', {}))
        self.assertEqual(self.opts('%1')['@cc_state'], 'idle')

    def test_dialogs_block_until_answered(self):
        snaps = self.drive(start(), tool('exec'), tool('ask_user'), SNAP,
                           tool('ask_user', 'result'), SNAP,
                           tool('secrets', args={'action': 'request'}), SNAP,
                           tool('secrets', args={'action': 'list'}), SNAP)['snaps']
        self.assertEqual([s['%1']['@cc_state'] for s in snaps],
                         ['blocked', 'working', 'blocked', 'working'])

    def test_direct_dialog_call_blocks_from_the_transcript(self):
        ask = {'event': 'session.message', 'payload': {'sessionKey': KEY_A, 'message': {
            'role': 'assistant', 'content': [{'type': 'toolCall', 'name': 'ask_user', 'arguments': {}}]}}}
        answered = {'event': 'session.message', 'payload': {'sessionKey': KEY_A, 'message': {
            'role': 'toolResult', 'toolName': 'ask_user', 'content': []}}}
        snaps = self.drive(start(), ask, SNAP, answered, SNAP)['snaps']
        self.assertEqual([s['%1']['@cc_state'] for s in snaps], ['blocked', 'working'])

    def test_session_updates_mid_dialog_keep_it_blocked(self):
        # Activity summaries and model calls arrive while the dialog is up.
        self.drive(start(), tool('ask_user'), changed(running=True, reason='activity-summary'),
                   changed(running=True, phase='model'))
        self.assertEqual(self.opts('%1')['@cc_state'], 'blocked')

    def test_question_failure_and_abort(self):
        snaps = self.drive(start(), end('**Which one?**'), SNAP,
                           start(), settle(status='failed'), SNAP,
                           start(), settle(status='killed', abortedLastRun=True), SNAP,
                           start(), settle(status='failed', abortedLastRun=True), SNAP)['snaps']
        self.assertEqual([s['%1']['@cc_state'] for s in snaps], ['asked', 'blocked', 'idle', 'idle'])

    def test_settle_without_a_seen_start_still_ends_the_turn(self):
        # The bridge connected mid-turn: the first thing it hears is the end.
        self.drive(reply('Done.'), settle())
        self.assertEqual(self.opts('%1')['@cc_state'], 'idle')

    def test_background_subagents_keep_the_tab_busy(self):
        child = 'agent:main:subagent:1'
        live = changed(child, True, spawnedBy=KEY_A, spawnDepth=1)
        done = changed(child, False, spawnedBy=KEY_A, spawnDepth=1, reason='agent.input.settled')
        # Live twice on purpose: the count is of distinct children.
        snaps = self.drive(start(), live, live, end('Started it.'), SNAP, done, SNAP)['snaps']
        self.assertEqual(snaps[0]['%1']['@cc_subs'], '1')
        self.assertEqual(snaps[0]['%1']['@cc_state'], 'working')
        self.assertEqual(snaps[1]['%1']['@cc_subs'], '0')
        self.assertEqual(snaps[1]['%1']['@cc_state'], 'idle')

    def test_label_names_the_window(self):
        snaps = self.drive(start(), SNAP, changed(reason='reset'))['snaps']
        self.assertEqual(snaps[0]['%1']['@cc_name'], 'openxr')
        self.assertNotIn('@cc_name', snaps[0]['%2'])
        self.assertGreater(self.read().get('renames', 0), 0)
        self.assertEqual(self.opts('%1')['@cc_name'], 'openxr')

    def test_new_transcript_resets_the_tab(self):
        # /new keeps the key and replaces the sessionId.
        self.drive(start(sessionId='s1'), end('**Which one?**'), changed(running=False, sessionId='s2'))
        self.assertNotIn('@cc_asked', self.opts('%1'))
        self.assertNotEqual(self.opts('%1').get('@cc_state'), 'asked')

    def test_switching_session_resets_the_tab(self):
        child = 'agent:main:subagent:2'
        # %1 is mid-turn on KEY_A when its TUI switches to KEY_B, which has a
        # background child running. The next look at the panes notices.
        self.drive(start(), end('**Which one?**'),
                   changed(child, True, spawnedBy=KEY_B, spawnDepth=1),
                   {'screen': {'pane': '%1', 'text': footer(KEY_B, 'other')}},
                   {'refresh': True})
        self.assertEqual(self.opts('%1')['@cc_name'], 'other')
        self.assertEqual(self.opts('%1')['@cc_subs'], '1')
        self.assertNotIn('@cc_asked', self.opts('%1'))
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')
        self.assertEqual(self.opts('%1')['@cc_colour'], '#bb9af7')

    def test_switching_to_a_running_session_shows_it_working(self):
        self.drive(start(KEY_B), {'screen': {'pane': '%1', 'text': footer(KEY_B)}}, {'refresh': True})
        self.assertEqual(self.opts('%1')['@cc_state'], 'working')

    def test_deleted_session_clears(self):
        self.drive(start(), end('Done.'), changed(reason='deleted'))
        self.assertNotIn('@cc_state', self.opts('%1'))
        self.assertNotIn('@cc_name', self.opts('%1'))

    def test_reconnect_snapshot_catches_up(self):
        # Seeded rows are sessions.subscribe snapshot rows: key, not sessionKey.
        row = {'key': KEY_A, 'hasActiveRun': True, 'status': 'running'}
        snaps = self.drive({'seed': [row]}, SNAP,
                           {'seed': [{**row, 'hasActiveRun': False, 'status': 'done'}]}, SNAP)['snaps']
        self.assertEqual([s['%1']['@cc_state'] for s in snaps], ['working', 'idle'])

    def test_reports_the_sessions_panes_show(self):
        self.assertEqual(sorted(self.drive({'refresh': True})['shown']), sorted([KEY_A, KEY_B]))

    def test_no_tmux_is_harmless(self):
        bin_dir = self.dir / 'no-tmux'
        bin_dir.mkdir()
        (bin_dir / 'bash').symlink_to(shutil.which('bash'))
        self.env['PATH'] = str(bin_dir)
        self.drive(start(), end('Done.'))


class TargetTests(unittest.TestCase):
    """Which Gateway the bridge connects to, from openclaw.json."""

    def target(self, config, env=None):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        script = ('const { gatewayTarget } = await import(process.argv[1]);'
                  'process.stdout.write(JSON.stringify(gatewayTarget(JSON.parse(process.argv[2]),'
                  ' JSON.parse(process.argv[3]))));')
        out = subprocess.run([node, '--input-type=module', '-e', script, '--', str(BRIDGE),
                              json.dumps(config), json.dumps(env or {})],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_local_gateway(self):
        got = self.target({'gateway': {'port': 19999, 'auth': {'token': 'local'}}})
        self.assertEqual((got['url'], got['token']), ('ws://127.0.0.1:19999', 'local'))

    def test_remote_gateway(self):
        got = self.target({'gateway': {'mode': 'remote', 'auth': {'token': 'local'},
                                       'remote': {'url': 'wss://claw.example', 'token': 'remote'}}})
        self.assertEqual((got['url'], got['token']), ('wss://claw.example', 'remote'))

    def test_secret_refs_and_env_override(self):
        got = self.target({'gateway': {'auth': {'token': {'source': 'env', 'id': 'X'}}}},
                          {'OPENCLAW_GATEWAY_TOKEN': 'from-env', 'OPENCLAW_TMUX_STATUS_URL': 'ws://h:1'})
        self.assertEqual((got['url'], got['token']), ('ws://h:1', 'from-env'))
        self.assertNotIn('token', self.target({'gateway': {'auth': {'token': {'source': 'env'}}}}))


if __name__ == '__main__':
    unittest.main()
