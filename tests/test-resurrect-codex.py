#!/usr/bin/env python3
"""Validate save-file rewrites against live process identities, never real sessions."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('restore', ROOT / 'config/codex/hooks/tmux-session.py')
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)
A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

class RestoreTests(unittest.TestCase):
    def test_same_directory_different_sessions_and_stale_records(self):
        with tempfile.TemporaryDirectory() as d:
            transcript = Path(d) / 'rollout.jsonl'
            transcript.write_text('{}\n')
            processes = {10: (1, '100', 'bash'), 11: (10, '101', 'codex'),
                         20: (1, '200', 'bash'), 21: (20, '201', 'codex')}
            panes = [{'pid': 10, 'address': ('main', '0', '0'), 'command': 'codex'},
                     {'pid': 20, 'address': ('main', '0', '1'), 'command': 'codex'}]
            records = [{'pid': 11, 'start': '101', 'session_id': A, 'transcript_path': str(transcript)},
                       {'pid': 21, 'start': '201', 'session_id': B, 'transcript_path': str(transcript)}]
            self.assertEqual(restore.session_map(records, panes, processes),
                             {('main', '0', '0'): A, ('main', '0', '1'): B})
            records[1]['start'] = 'old'
            self.assertEqual(restore.session_map(records, panes, processes), {('main', '0', '0'): A})
            records[0]['session_id'] = '$(touch /tmp/no)'
            self.assertEqual(restore.session_map(records, panes, processes), {})

    def test_ambiguous_missing_transcript_and_background_process(self):
        with tempfile.TemporaryDirectory() as d:
            transcript = Path(d) / 'rollout.jsonl'
            transcript.touch()
            processes = {10: (1, '100', 'bash'), 11: (10, '101', 'codex'), 12: (10, '102', 'codex')}
            pane = {'pid': 10, 'address': ('main', '0', '0'), 'command': 'codex'}
            record = {'pid': 11, 'start': '101', 'session_id': A, 'transcript_path': str(transcript)}
            self.assertEqual(restore.session_map([record], [pane], processes), {})
            del processes[12]
            pane['command'] = 'bash'
            self.assertEqual(restore.session_map([record], [pane], processes), {})
            pane['command'] = 'codex'
            transcript.unlink()
            self.assertEqual(restore.session_map([record], [pane], processes), {})

    def test_rewrite_preserves_every_other_field(self):
        row = 'pane\tmain\t0\t1\t-\t0\t:/project\t1\t0\tcodex\t:/usr/bin/codex\n'
        unrelated = row.replace('codex', 'fish')
        expected = row.rsplit('\t', 1)[0] + '\t:codex resume ' + A + '\n'
        mapping = {('main', '0', '0'): A}
        self.assertEqual(restore.rewrite(row + unrelated, mapping), expected + unrelated)
        self.assertEqual(restore.rewrite(expected, mapping), expected)

if __name__ == '__main__':
    unittest.main()
