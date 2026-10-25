#!/usr/bin/env python3
"""OpenCode tmux status and restore, against a fake tmux; never the user's server.

The plugin's state machine (config/opencode/plugins/tmux-status/status.ts) is
driven through node with a stand-in for the TUI's session data, the real
renderer and the real resurrect hook, so this covers the path from a session's
state to the pane options powerkit reads. The OpenCode adapter in tui.ts is not
exercised here: it needs a running TUI.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / 'config/opencode/plugins/tmux-status/status.ts'
RENDERER = ROOT / 'config/.claude/hooks/tmux-status.sh'
SAVE = ROOT / 'home/.tmux/resurrect-opencode.sh'

FAKE_TMUX = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
p = Path(os.environ['TEST_STATE'])
s = json.loads(p.read_text())
a = sys.argv[1:]
s['calls'] = s.get('calls', 0) + 1
s.setdefault('log', []).append(a)
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
p.write_text(json.dumps(s))
'''

# A stand-in for the TUI's session cache: `world` maps a session id to its
# parent, run state, pending dialogs and last reply. Each step edits it, then
# refreshes, the way an OpenCode event or the poll would.
DRIVER = r'''
const [statusPath, renderer, stepsJson] = process.argv.slice(1);
const { createTmuxStatus } = await import(statusPath);
const { readFileSync } = await import("node:fs");
const state = () => JSON.parse(readFileSync(process.env.TEST_STATE, "utf8"));
let route;
const sessions = {};
const root = (id) => { while (sessions[id]?.parent) id = sessions[id].parent; return id; };
const data = {
  route: () => route,
  root,
  family: (id) => Object.keys(sessions).filter((s) => root(s) === id),
  running: (id) => !!sessions[id]?.running,
  queued: (id) => !!sessions[id]?.queued,
  waiting: (id) => !!sessions[id]?.waiting,
  failed: (id) => !!sessions[id]?.failed,
  lastReply: async (id) => sessions[id]?.reply ?? "",
};
const status = createTmuxStatus({ pane: "%1", renderer, data });
const snaps = [];
for (const step of JSON.parse(stepsJson)) {
  if ("route" in step) route = step.route ?? undefined;
  for (const [id, patch] of Object.entries(step.sessions ?? {})) sessions[id] = { ...sessions[id], ...patch };
  if (step.snap) {
    const s = state();
    snaps.push({ opts: s.panes["%1"]?.opts ?? {}, calls: s.calls ?? 0 });
    continue;
  }
  if (step.close) await status.close();
  else await status.refresh();
}
process.stdout.write(JSON.stringify(snaps));
'''

ROOT_ID = 'ses_e8aee566bffeqySoAsgfjFnhZJ'
CHILD_ID = 'ses_e8af00000001AbCdEfGhIjKlMn'
OTHER_ID = 'ses_e8b100000002ZyXwVuTsRqPoNm'


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
        self.panes({'%1': {'cmd': 'opencode'}})

    def panes(self, panes):
        self.state.write_text(json.dumps({'panes': panes}))

    def read(self):
        return json.loads(self.state.read_text())


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class StatusTests(Base):
    def drive(self, steps):
        out = subprocess.run(['node', '--input-type=module', '-e', DRIVER, '--',
                              str(STATUS), str(RENDERER), json.dumps(steps)],
                             env=self.env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_home_screen_leaves_no_state(self):
        [snap] = self.drive([{'route': None}, {'snap': True}])
        self.assertEqual(snap['opts'], {})

    def test_session_shown_is_idle_and_recorded(self):
        [snap] = self.drive([{'route': ROOT_ID, 'sessions': {ROOT_ID: {}}}, {'snap': True}])
        self.assertEqual(snap['opts']['@cc_state'], 'idle')
        self.assertEqual(snap['opts']['@oc_session'], ROOT_ID)

    def test_turn_runs_then_finishes(self):
        working, done = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {}}},
            {'sessions': {ROOT_ID: {'running': True}}}, {'snap': True},
            {'sessions': {ROOT_ID: {'running': False, 'reply': 'Done.'}}}, {'snap': True},
        ])
        self.assertEqual(working['opts']['@cc_state'], 'working')
        self.assertEqual(working['opts']['@cc_colour'], 'colour216')
        self.assertEqual(done['opts']['@cc_state'], 'idle')

    def test_queued_prompt_counts_as_working(self):
        [snap] = self.drive([{'route': ROOT_ID, 'sessions': {ROOT_ID: {'queued': True}}},
                             {'snap': True}])
        self.assertEqual(snap['opts']['@cc_state'], 'working')

    def test_subagent_turns_purple_and_outlives_the_turn(self):
        subs, outlived, finished = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True}}},
            {'sessions': {CHILD_ID: {'parent': ROOT_ID, 'running': True}}}, {'snap': True},
            {'sessions': {ROOT_ID: {'running': False}}}, {'snap': True},
            {'sessions': {CHILD_ID: {'running': False}}}, {'snap': True},
        ])
        self.assertEqual(subs['opts']['@cc_subs'], '1')
        self.assertEqual(subs['opts']['@cc_colour'], '#bb9af7')
        self.assertEqual(outlived['opts']['@cc_state'], 'working')
        self.assertEqual(finished['opts']['@cc_state'], 'idle')

    def test_dialog_in_a_subagent_blocks(self):
        blocked, answered = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True},
                                            CHILD_ID: {'parent': ROOT_ID, 'running': True}}},
            {'sessions': {CHILD_ID: {'waiting': True}}}, {'snap': True},
            {'sessions': {CHILD_ID: {'waiting': False}}}, {'snap': True},
        ])
        self.assertEqual(blocked['opts']['@cc_state'], 'blocked')
        self.assertEqual(answered['opts']['@cc_state'], 'working')

    def test_blocked_survives_a_subagent_change(self):
        [snap] = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True, 'waiting': True}}},
            {'sessions': {CHILD_ID: {'parent': ROOT_ID, 'running': True}}}, {'snap': True},
        ])
        self.assertEqual(snap['opts']['@cc_state'], 'blocked')
        self.assertEqual(snap['opts']['@cc_subs'], '1')

    def test_turn_ending_on_a_question_asks(self):
        [snap] = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True}}},
            {'sessions': {ROOT_ID: {'running': False, 'reply': 'Two options.\n\n**Which one?**'}}},
            {'snap': True},
        ])
        self.assertEqual(snap['opts']['@cc_state'], 'asked')

    def test_failed_turn_is_red(self):
        [snap] = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True}}},
            {'sessions': {ROOT_ID: {'running': False, 'failed': True}}}, {'snap': True},
        ])
        self.assertEqual(snap['opts']['@cc_state'], 'blocked')

    def test_subagent_view_follows_its_root(self):
        [snap] = self.drive([
            {'route': CHILD_ID, 'sessions': {ROOT_ID: {'running': True},
                                             CHILD_ID: {'parent': ROOT_ID, 'running': True}}},
            {'snap': True},
        ])
        self.assertEqual(snap['opts']['@oc_session'], ROOT_ID)
        self.assertEqual(snap['opts']['@cc_subs'], '1')

    def test_switching_sessions_resets(self):
        [snap] = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True},
                                            CHILD_ID: {'parent': ROOT_ID, 'running': True}}},
            {'route': OTHER_ID, 'sessions': {OTHER_ID: {}}}, {'snap': True},
        ])
        self.assertEqual(snap['opts']['@cc_state'], 'idle')
        self.assertEqual(snap['opts']['@cc_subs'], '0')
        self.assertEqual(snap['opts']['@oc_session'], OTHER_ID)

    def test_home_screen_and_close_clear_the_pane(self):
        home, closed = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True}}},
            {'route': None}, {'snap': True},
            {'route': ROOT_ID}, {'close': True}, {'snap': True},
        ])
        self.assertEqual(home['opts'], {})
        self.assertEqual(closed['opts'], {})

    def test_unchanged_state_costs_nothing(self):
        before, after = self.drive([
            {'route': ROOT_ID, 'sessions': {ROOT_ID: {'running': True}}}, {'snap': True},
            {}, {}, {}, {'snap': True},
        ])
        self.assertEqual(before['calls'], after['calls'])


class RendererTests(Base):
    def test_next_jumps_to_an_idle_opencode_pane(self):
        self.panes({'%1': {'cmd': 'fish'},
                    '%2': {'cmd': 'opencode', 'session': 'work', 'window': '3',
                           'opts': {'@cc_state': 'idle'}}})
        subprocess.run(['bash', str(RENDERER), 'next'], env={**self.env, 'TMUX_PANE': '%1'},
                       check=True)
        self.assertIn(['select-pane', '-t', '%2'], self.read()['log'])

    def test_off_drops_the_recorded_session(self):
        self.panes({'%1': {'cmd': 'opencode', 'opts': {'@cc_state': 'idle', '@oc_session': ROOT_ID}}})
        subprocess.run(['bash', str(RENDERER), 'off'], env={**self.env, 'TMUX_PANE': '%1'},
                       check=True)
        self.assertEqual(self.read()['panes']['%1']['opts'], {})


class SaveTests(Base):
    def test_rewrites_placeable_panes_only(self):
        self.panes({'%1': {'cmd': 'opencode', 'session': 'main', 'window': '5', 'index': '1',
                           'opts': {'@oc_session': ROOT_ID}},
                    '%2': {'cmd': 'opencode', 'session': 'main', 'window': '6', 'index': '0'},
                    '%3': {'cmd': 'opencode', 'session': 'main', 'window': '7', 'index': '0',
                           'opts': {'@oc_session': 'ses_x; rm -rf ~'}},
                    '%4': {'cmd': 'fish', 'session': 'main', 'window': '8', 'index': '0',
                           'opts': {'@oc_session': OTHER_ID}}})

        def line(window, index, cmd, full):
            return '\t'.join(['pane', 'main', window, '1', ':*', index, 'title', ':/home/d', '1', cmd, full])
        save = self.dir / 'save.txt'
        rows = [line('5', '1', 'opencode', ':opencode'), line('6', '0', 'opencode', ':opencode'),
                line('7', '0', 'opencode', ':opencode'), line('8', '0', 'fish', ':fish'),
                'window\tmain\t5\t:x\t1\t:*\tlayout\t:']
        save.write_text('\n'.join(rows) + '\n')
        subprocess.run(['bash', str(SAVE), str(save)], env=self.env, check=True)
        got = [r.split('\t') for r in save.read_text().splitlines()]
        self.assertEqual(got[0][10], ':opencode --session ' + ROOT_ID)
        self.assertEqual(got[1][10], ':opencode')
        self.assertEqual(got[2][10], ':opencode')
        self.assertEqual(got[3][10], ':fish')
        self.assertEqual(len(got), len(rows))
        self.assertEqual(list(self.dir.glob('save.txt.opencode.*')), [])


if __name__ == '__main__':
    unittest.main()
