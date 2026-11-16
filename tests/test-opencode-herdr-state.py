#!/usr/bin/env python3
"""The patched herdr OpenCode plugin, against a stand-in OpenCode and herdr.

config/opencode/herdr-tui-session.js is herdr's OpenCode TUI integration with
one change: a pane stays `working` while a subagent under the session on screen
is still running. herdr's own copy calls the pane idle as soon as the parent's
turn ends, so the sidebar marks it done while the work goes on (herdr #4511).

The plugin's OpenCode 2.x entry point, setup(), is driven through node with a
stand-in for the TUI's session data and a unix socket that answers the way
herdr's does and records each state report, so neither a TUI nor a herdr
server is touched. Date.now is skewed to step past the plugin's settle window
without waiting it out.

The last two tests run herdr's own copy, which `herdr integration install`
writes into an empty $HOME: one fails once herdr ships a newer integration
than the one patched here, the other once herdr's copy handles this case
itself, at which point the override should go. Both need a real herdr, so CI
skips them.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'config/opencode/herdr-tui-session.js'

# Each step edits the stand-in session data, then optionally starts the plugin,
# fires one event at it, or skews the clock, then waits for its 100 ms poll to
# catch up. Prints, per step, the states the plugin reported during it.
DRIVER = r'''
const [modulePath, socketPath, stepsJson] = process.argv.slice(1);
process.env.HERDR_ENV = "1";
process.env.HERDR_SOCKET_PATH = socketPath;
process.env.HERDR_PANE_ID = "w1:p1";
const realNow = Date.now;
let skew = 0;
Date.now = () => realNow() + skew;

const net = await import("node:net");
let received = [];
const server = net.createServer((conn) => {
  let buffer = "";
  conn.on("error", () => {});
  conn.on("data", (chunk) => {
    buffer += chunk;
    const end = buffer.indexOf("\n");
    if (end < 0) return;
    const request = JSON.parse(buffer.slice(0, end));
    if (request.method === "pane.report_agent") received.push(request.params.state);
    conn.end('{"id":"x","result":{"type":"ok"}}\n');
  });
});
await new Promise((resolve) => server.listen(socketPath, resolve));

const { default: plugin } = await import(modulePath);
let route;
const sessions = {};
const active = {};
const permissions = {};
const listeners = new Set();
const root = (id) => { while (sessions[id]?.parentID) id = sessions[id].parentID; return id; };
const api = {
  ui: { router: { current: () => (route ? { type: "session", sessionID: route } : { type: "home" }) } },
  data: {
    session: {
      get: (id) => (sessions[id] ? { id, ...sessions[id] } : undefined),
      family: (id) => Object.keys(sessions).filter((s) => root(s) === root(id)),
      status: (id) => active[id] ?? "idle",
      permission: { list: (id) => permissions[id] ?? [] },
      form: { list: () => [] },
    },
    listen(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  },
};

let dispose;
const reports = [];
for (const step of JSON.parse(stepsJson)) {
  for (const [id, patch] of Object.entries(step.sessions ?? {})) sessions[id] = { ...sessions[id], ...patch };
  for (const [id, status] of Object.entries(step.status ?? {})) {
    if (status === "running") active[id] = status;
    else delete active[id];
  }
  for (const [id, list] of Object.entries(step.permissions ?? {})) permissions[id] = list;
  if ("route" in step) route = step.route ?? undefined;
  if (step.advance) skew += step.advance;
  if (step.start) dispose = plugin.setup(api);
  if (step.event) for (const fn of listeners) fn({ details: step.event });
  await new Promise((resolve) => setTimeout(resolve, step.wait ?? 300));
  reports.push(received);
  received = [];
}
dispose?.();
process.stdout.write(JSON.stringify(reports));
process.exit(0);
'''

R = 'ses_e23f62b41ffeeOU8j8P7lHh5YX'
C = 'ses_e23f700000001AbCdEfGhIjKlM'


def event(kind, session, **data):
    return {'type': kind, 'data': {'sessionID': session, **data}}


def started(session):
    return event('session.execution.started', session)


def succeeded(session):
    return event('session.execution.succeeded', session)


# The parent starts a turn and hands work to a background subagent.
DELEGATE = [
    {'sessions': {R: {}}, 'route': R, 'status': {R: 'running'}, 'start': True},
    {'event': started(R)},
    {'sessions': {C: {'parentID': R}}, 'status': {C: 'running'},
     'event': event('session.created', C, parentID=R)},
    {'status': {C: 'running'}, 'event': started(C)},
]

# The parent's turn ends while the subagent is still running: the
# "↓ 1 subagent" footer, after a real pane.
PARENT_ENDS = [
    {'status': {R: 'idle'}, 'event': succeeded(R)},
    {'wait': 500},
]

# The subagent reports back and the parent takes up its result.
CHILD_DONE_PARENT_RESUMES = [
    {'status': {C: 'idle'}, 'event': succeeded(C)},
    {'status': {R: 'running'}, 'event': started(R)},
    {'status': {R: 'idle'}, 'event': succeeded(R)},
]


def flatten(reports):
    """Every state reported, in order, with repeats of the same state dropped."""
    out = []
    for state in (s for step in reports for s in step):
        if not out or out[-1] != state:
            out.append(state)
    return out


def drive(plugin, steps):
    with tempfile.TemporaryDirectory() as directory:
        out = subprocess.run(['node', '--input-type=module', '-e', DRIVER, '--',
                              str(plugin), str(Path(directory) / 'herdr.sock'),
                              json.dumps(steps)],
                             capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class SubagentStateTests(unittest.TestCase):
    def test_parent_turn_ending_under_a_running_subagent_stays_working(self):
        reports = drive(PLUGIN, DELEGATE + PARENT_ENDS)
        self.assertEqual(flatten(reports), ['working'])

    def test_whole_delegation_reads_done_once_at_the_end(self):
        reports = drive(PLUGIN, DELEGATE + PARENT_ENDS + CHILD_DONE_PARENT_RESUMES)
        self.assertEqual(flatten(reports), ['working', 'idle'])
        # Done as soon as the parent's last turn ends, not after a settle window.
        self.assertIn('idle', reports[-1])

    def test_gap_before_the_parent_resumes_is_not_done(self):
        # The subagent has ended and the parent has not started on its result
        # yet: nothing is running for a moment.
        steps = DELEGATE + PARENT_ENDS + [
            {'status': {C: 'idle'}, 'event': succeeded(C), 'wait': 500},
        ]
        self.assertEqual(flatten(drive(PLUGIN, steps)), ['working'])

    def test_parent_that_never_resumes_reads_done_after_the_settle_window(self):
        steps = DELEGATE + PARENT_ENDS + [
            {'status': {C: 'idle'}, 'event': succeeded(C)},
            {'advance': 5_000},
        ]
        reports = drive(PLUGIN, steps)
        self.assertEqual(flatten(reports), ['working', 'idle'])
        self.assertIn('idle', reports[-1])

    def test_turn_without_subagents_reads_done_at_once(self):
        steps = [
            {'sessions': {R: {}}, 'route': R, 'status': {R: 'running'}, 'start': True},
            {'event': started(R)},
            {'status': {R: 'idle'}, 'event': succeeded(R)},
        ]
        reports = drive(PLUGIN, steps)
        self.assertEqual(flatten(reports), ['working', 'idle'])
        self.assertIn('idle', reports[-1])

    def test_opening_a_session_whose_subagent_is_running_reads_working(self):
        steps = [{'sessions': {R: {}, C: {'parentID': R}}, 'route': R,
                  'status': {C: 'running'}, 'start': True}]
        self.assertEqual(flatten(drive(PLUGIN, steps)), ['working'])

    def test_subagent_permission_answered_goes_back_to_working(self):
        ask = {'id': 'per_1', 'sessionID': C}
        steps = DELEGATE + PARENT_ENDS + [
            {'permissions': {C: [ask]}, 'event': event('permission.asked', C, id='per_1')},
            {'permissions': {C: []}, 'event': event('permission.replied', C, requestID='per_1')},
        ]
        reports = drive(PLUGIN, steps)
        self.assertEqual(flatten(reports), ['working', 'blocked', 'working'])

    def test_viewing_the_subagent_follows_the_parent(self):
        steps = DELEGATE + PARENT_ENDS + [{'route': C, 'wait': 500}]
        self.assertEqual(flatten(drive(PLUGIN, steps)), ['working'])


def integration_version(text):
    match = re.search(r'^// HERDR_INTEGRATION_VERSION=(\d+)$', text, re.M)
    return match and int(match.group(1))


@unittest.skipUnless(shutil.which('node') and shutil.which('herdr'), 'needs node and herdr')
class HerdrOwnCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        home = Path(cls.directory.name)
        (home / '.config/opencode').mkdir(parents=True)
        env = {'PATH': os.environ['PATH'], 'HOME': str(home),
               'XDG_CONFIG_HOME': str(home / '.config'),
               'XDG_STATE_HOME': str(home / '.local/state')}
        out = subprocess.run(['herdr', 'integration', 'install', 'opencode'],
                             env=env, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        cls.stock = home / '.config/opencode/herdr-tui-session.js'

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_patch_is_based_on_the_integration_herdr_ships(self):
        theirs = integration_version(self.stock.read_text())
        ours = integration_version(PLUGIN.read_text())
        self.assertEqual(
            ours, theirs,
            f'herdr now ships OpenCode TUI integration v{theirs}; the override is '
            f'v{ours}. If herdr #4511 is fixed there, delete '
            f'config/opencode/herdr-tui-session.js; otherwise redo the patch on '
            f'top of the new version.')

    def test_herdr_still_calls_the_pane_done_under_a_running_subagent(self):
        reports = drive(self.stock, DELEGATE + PARENT_ENDS)
        self.assertIn(
            'idle', flatten(reports),
            "herdr's own OpenCode plugin now keeps the pane working while a "
            'subagent runs (herdr #4511); delete config/opencode/herdr-tui-session.js.')


if __name__ == '__main__':
    unittest.main()
