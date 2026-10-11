#!/usr/bin/env python3
"""Exercise the installed resurrect save/restore scripts on an isolated tmux server."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = Path.home() / '.tmux/plugins/tmux-resurrect/scripts'

@unittest.skipUnless((PLUGIN / 'save.sh').is_file() and Path('/proc/self/stat').exists(),
                     'needs Linux and the installed tmux-resurrect plugin')
class IntegrationTests(unittest.TestCase):
    def test_two_panes_resume_their_own_conversations(self):
        with tempfile.TemporaryDirectory(prefix='codex-resurrect-') as temporary:
            directory = Path(temporary)
            for name in ('bin', 'state', 'project', 'resurrect', 'home'):
                (directory / name).mkdir()
            (directory / 'home/.tmux').symlink_to(ROOT / 'home/.tmux')
            socket = directory / 'socket'
            log = directory / 'launches'
            transcript = directory / 'rollout.jsonl'
            transcript.write_text('{}\n')
            codex = directory / 'bin/codex'
            fake = directory / 'bin/fake.py'
            fake.write_text('''#!/usr/bin/env python3
import ctypes, json, os, subprocess, sys, time
ctypes.CDLL(None).prctl(15, b'codex', 0, 0, 0)
with open(os.environ['LAUNCH_LOG'], 'a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
session = sys.argv[-1]
time.sleep(.2)
subprocess.run(['python3', os.environ['REGISTRAR']], input=json.dumps({
    'session_id': session, 'transcript_path': os.environ['TRANSCRIPT'],
    'hook_event_name': 'SessionStart'}), text=True)
time.sleep(120)
''')
            codex.write_text('#!/bin/bash\nexec -a codex /usr/bin/python3 ' + shlex.quote(str(fake)) + ' "$@"\n')
            codex.chmod(0o755)
            env = {**os.environ, 'HOME': str(directory / 'home'),
                   'CODEX_HOME': str(ROOT / 'config/codex'),
                   'XDG_STATE_HOME': str(directory / 'state'),
                   'TMUX_TMPDIR': str(directory), 'LAUNCH_LOG': str(log),
                   'REGISTRAR': str(ROOT / 'config/codex/hooks/tmux-session.py'),
                   'TRANSCRIPT': str(transcript), 'PATH': str(directory / 'bin') + ':' + os.environ['PATH']}
            env.pop('TMUX', None)
            env.pop('TMUX_PANE', None)
            def tmux(*args):
                return subprocess.run(['tmux', '-S', str(socket), *args], env=env,
                                      capture_output=True, text=True, check=True).stdout.strip()
            def wait_for(condition):
                for _ in range(100):
                    if condition():
                        return
                    time.sleep(.1)
                self.fail('Timed out waiting for stand-in Codex processes: ' +
                          tmux('list-panes', '-a', '-F', '#{pane_pid} #{pane_current_command}') +
                          '\n' + tmux('capture-pane', '-p', '-t', 'main:0.0'))
            rc = directory / 'rc'
            rc.write_text('export PATH=' + shlex.quote(str(directory / 'bin')) + ':$PATH\n')
            shell = '/bin/bash --noprofile --rcfile ' + shlex.quote(str(rc))
            config = directory / 'restore.conf'
            config.write_text('\n'.join(line for line in (ROOT / 'home/.tmux.conf').read_text().splitlines()
                                        if line.startswith(('set -g @resurrect-processes ',
                                                            'set -g @resurrect-hook-post-save-layout '))))
            def start(name):
                tmux('-f', '/dev/null', 'new-session', '-d', '-s', name,
                     '-c', str(directory / 'project'), shell)
                tmux('set-option', '-g', 'default-command', shell)
                tmux('set-option', '-g', '@resurrect-dir', str(directory / 'resurrect'))
                tmux('source-file', str(config))
            ids = ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']
            try:
                start('main')
                tmux('split-window', '-t', 'main:0', '-c', str(directory / 'project'))
                for pane, session in enumerate(ids):
                    tmux('send-keys', '-t', f'main:0.{pane}', 'codex ' + session, 'Enter')
                wait_for(lambda: len(list((directory / 'state').glob('codex/tmux-sessions/*/*.json'))) == 2)
                tmux('run-shell', str(PLUGIN / 'save.sh') + ' quiet')
                saved = (directory / 'resurrect/last').resolve().read_text()
                for session in ids:
                    self.assertIn(':codex resume ' + session, saved)
                tmux('kill-server')
                time.sleep(.2)
                log.unlink()
                start('boot')
                tmux('run-shell', str(PLUGIN / 'restore.sh'))
                wait_for(lambda: log.exists() and len(log.read_text().splitlines()) == 2)
                self.assertEqual(sorted(json.loads(line) for line in log.read_text().splitlines()),
                                 [['resume', session] for session in ids])
            finally:
                subprocess.run(['tmux', '-S', str(socket), 'kill-server'], capture_output=True)

if __name__ == '__main__':
    unittest.main()
