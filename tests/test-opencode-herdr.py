#!/usr/bin/env python3
"""OpenCode's session name for herdr, against a stand-in herdr socket.

config/opencode/plugins/herdr-session/name.ts is driven through node with a
stand-in for the TUI's session data and a unix socket that answers the way
herdr's does and records each request, so no herdr server is touched. The
OpenCode adapter in tui.ts is not exercised here: it needs a running TUI.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NAME = ROOT / 'config/opencode/plugins/herdr-session/name.ts'

# `sessions` maps a session id to its parent and title. Each step edits them,
# then refreshes (or closes), the way the plugin's poll would. With
# `answering` false the socket takes requests but never replies, as a stuck
# herdr would; those are not recorded.
DRIVER = r'''
const [modulePath, socketPath, stepsJson] = process.argv.slice(1);
const { createHerdrSession } = await import(modulePath);
const net = await import("node:net");
const received = [];
let answering = true;
const server = net.createServer((conn) => {
  let buffer = "";
  conn.on("error", () => {});
  conn.on("data", (chunk) => {
    buffer += chunk;
    const end = buffer.indexOf("\n");
    if (end < 0 || !answering) return;
    received.push(JSON.parse(buffer.slice(0, end)));
    conn.end('{"id":"x","result":{"type":"ok"}}\n');
  });
});
await new Promise((resolve) => server.listen(socketPath, resolve));
let route;
const sessions = {};
const data = {
  route: () => route,
  root: (id) => { while (sessions[id]?.parent) id = sessions[id].parent; return id; },
  title: (id) => sessions[id]?.title,
};
const herdr = createHerdrSession({ socket: socketPath, pane: "w1:p1", data });
for (const step of JSON.parse(stepsJson)) {
  if ("route" in step) route = step.route ?? undefined;
  for (const [id, patch] of Object.entries(step.sessions ?? {})) sessions[id] = { ...sessions[id], ...patch };
  if ("answering" in step) answering = step.answering;
  if (step.close) await herdr.close();
  else await herdr.refresh();
}
process.stdout.write(JSON.stringify(received.map((r) => [r.method, r.params])));
process.exit(0);
'''

ROOT_ID = 'ses_e8aee566bffeqySoAsgfjFnhZJ'
CHILD_ID = 'ses_e8af00000001AbCdEfGhIjKlMn'
OTHER_ID = 'ses_e8b100000002ZyXwVuTsRqPoNm'


def sent(name):
    """The request that sets $session to `name` (or clears it, for '')."""
    return ['pane.report_metadata',
            {'pane_id': 'w1:p1', 'source': 'dotfiles:opencode', 'tokens': {'session': name}}]


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class HerdrSessionTests(unittest.TestCase):
    def drive(self, steps):
        with tempfile.TemporaryDirectory() as directory:
            out = subprocess.run(['node', '--input-type=module', '-e', DRIVER, '--',
                                  str(NAME), str(Path(directory) / 'herdr.sock'),
                                  json.dumps(steps)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_home_screen_reports_nothing(self):
        self.assertEqual(self.drive([{'route': None}]), [])

    def test_the_title_on_screen_is_reported_once(self):
        steps = [{'route': ROOT_ID, 'sessions': {ROOT_ID: {'title': 'Eden libretro core port'}}},
                 {}, {}]
        self.assertEqual(self.drive(steps), [sent('Eden libretro core port')])

    def test_a_new_title_is_reported(self):
        steps = [{'route': ROOT_ID, 'sessions': {ROOT_ID: {'title': 'first'}}},
                 {'sessions': {ROOT_ID: {'title': 'renamed'}}}]
        self.assertEqual(self.drive(steps), [sent('first'), sent('renamed')])

    def test_switching_sessions_reports_the_other_title(self):
        steps = [{'route': ROOT_ID, 'sessions': {ROOT_ID: {'title': 'one'},
                                                 OTHER_ID: {'title': 'two'}}},
                 {'route': OTHER_ID}]
        self.assertEqual(self.drive(steps), [sent('one'), sent('two')])

    def test_a_placeholder_title_is_no_name(self):
        steps = [{'route': ROOT_ID,
                  'sessions': {ROOT_ID: {'title': 'New session - 2026-11-12T08:15:02.123Z'}}}]
        self.assertEqual(self.drive(steps), [])

    def test_a_subagent_on_screen_names_its_root(self):
        steps = [{'route': CHILD_ID,
                  'sessions': {ROOT_ID: {'title': 'the conversation'},
                               CHILD_ID: {'parent': ROOT_ID, 'title': 'a subtask'}}}]
        self.assertEqual(self.drive(steps), [sent('the conversation')])

    def test_a_name_is_one_line(self):
        steps = [{'route': ROOT_ID, 'sessions': {ROOT_ID: {'title': ' two\n  lines '}}}]
        self.assertEqual(self.drive(steps), [sent('two lines')])

    def test_the_home_screen_clears_the_name(self):
        steps = [{'route': ROOT_ID, 'sessions': {ROOT_ID: {'title': 'one'}}},
                 {'route': None}]
        self.assertEqual(self.drive(steps), [sent('one'), sent('')])

    def test_closing_clears_the_name(self):
        steps = [{'route': ROOT_ID, 'sessions': {ROOT_ID: {'title': 'one'}}},
                 {'close': True}]
        self.assertEqual(self.drive(steps), [sent('one'), sent('')])

    def test_a_report_herdr_never_answered_is_sent_again(self):
        steps = [{'answering': False, 'route': ROOT_ID, 'sessions': {ROOT_ID: {'title': 'one'}}},
                 {'answering': True}]
        self.assertEqual(self.drive(steps), [sent('one')])


if __name__ == '__main__':
    unittest.main()
