#!/usr/bin/env python3
"""Bind Codex conversation IDs to live tmux processes for cautious resurrection."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')

def process_table():
    result = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            value = path.read_text()
            fields = value.rsplit(') ', 1)[1].split()
            result[int(path.parent.name)] = (int(fields[1]), fields[19], value.split('(', 1)[1].rsplit(')', 1)[0])
        except (OSError, ValueError, IndexError):
            continue
    return result

def pane_of(pid, panes, processes):
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        for pane in panes:
            if pane['pid'] == pid:
                return pane
        if pid not in processes:
            return None
        pid = processes[pid][0]
    return None

def codex_pids(pane, panes, processes):
    return [pid for pid, (_, _, command) in processes.items()
            if command == 'codex' and pane_of(pid, panes, processes) == pane]

def session_map(records, panes, processes):
    claims = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        pid, session = record.get('pid'), record.get('session_id', '')
        if not isinstance(pid, int) or not isinstance(session, str) or not UUID.fullmatch(session):
            continue
        process = processes.get(pid)
        if not process or process[1] != record.get('start') or process[2] != 'codex':
            continue
        pane = pane_of(pid, panes, processes)
        if not pane or pane['command'] != 'codex' or codex_pids(pane, panes, processes) != [pid]:
            continue
        transcript = record.get('transcript_path')
        if not isinstance(transcript, str) or not Path(transcript).is_file():
            continue
        claims.setdefault(pane['address'], []).append(session)
    return {address: ids[0] for address, ids in claims.items() if len(ids) == 1}

def rewrite(text, mapping):
    lines = []
    for line in text.splitlines(keepends=True):
        fields = line.rstrip('\n').split('\t')
        if len(fields) == 11 and fields[0] == 'pane' and fields[9] == 'codex':
            session = mapping.get((fields[1], fields[2], fields[5]))
            if session:
                fields[10] = ':codex resume ' + session
                line = '\t'.join(fields) + ('\n' if line.endswith('\n') else '')
        lines.append(line)
    return ''.join(lines)

def tmux(*args):
    return subprocess.run(['tmux', *args], check=True, capture_output=True, text=True, timeout=2).stdout.strip()

def main():
    # Hooks run in Codex's app-server daemon, which need not have started
    # inside tmux. The adapter passes the pane it found as TMUX_PANE.
    if not (os.environ.get('TMUX') or os.environ.get('TMUX_PANE')) or not Path('/proc/self/stat').is_file():
        return
    socket = tmux('display-message', '-p', '#{socket_path}')
    # Separate registries for separate tmux servers, including isolated tests.
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    directory = state / 'codex/tmux-sessions' / hashlib.sha256(socket.encode()).hexdigest()[:20]
    panes = []
    for line in tmux('list-panes', '-a', '-F', '#{pane_pid}\t#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_current_command}\t#{pane_id}').splitlines():
        fields = line.split('\t')
        if len(fields) == 6:
            panes.append({'pid': int(fields[0]), 'address': tuple(fields[1:4]), 'command': fields[4], 'id': fields[5]})
    processes = process_table()
    if len(sys.argv) == 3 and sys.argv[1] == '--save':
        records = []
        for path in directory.glob('*.json'):
            try:
                records.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        mapping = session_map(records, panes, processes)
        path = Path(sys.argv[2])
        if not mapping or not path.is_file():
            return
        before = path.read_text()
        after = rewrite(before, mapping)
        if after == before:
            return
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.codex.', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                output.write(after)
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or payload.get('agent_id'):
        return
    session = payload.get('session_id', '')
    if not isinstance(session, str) or not UUID.fullmatch(session):
        return
    pane = next((p for p in panes if p['id'] == os.environ.get('TMUX_PANE')), None)
    if not pane:
        return
    record_path = directory / (pane['id'].lstrip('%') + '.json')
    event = payload.get('hook_event_name')
    if event == 'SessionEnd':
        if record_path.is_file() and json.loads(record_path.read_text()).get('session_id') == session:
            record_path.unlink()
        return
    if event not in ('SessionStart', 'UserPromptSubmit', 'Stop'):
        return
    candidates = codex_pids(pane, panes, processes)
    if len(candidates) != 1 or pane['command'] != 'codex':
        record_path.unlink(missing_ok=True)
        return
    pid = candidates[0]
    transcript = payload.get('transcript_path')
    if not isinstance(transcript, str) or not transcript:
        home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        matches = list((home / 'sessions').glob('**/*' + session + '*.jsonl'))
        transcript = str(matches[0]) if len(matches) == 1 else ''
    record = {'pid': pid, 'start': processes[pid][1], 'session_id': session, 'transcript_path': transcript}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(record, output)
        os.replace(temporary, record_path)
    finally:
        Path(temporary).unlink(missing_ok=True)

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        # Saving UI state must never interrupt Codex or continuum.
        pass
