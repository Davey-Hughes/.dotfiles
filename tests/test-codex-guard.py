#!/usr/bin/env python3
"""Exercise adapter decisions without executing any supplied shell commands."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "config/codex/hooks/destructive-guard.py"
spec = importlib.util.spec_from_file_location("codex_guard", ADAPTER)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def payload(command):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                       "cwd": "/home/u/project"})


class GuardTests(unittest.TestCase):
    def assert_denied(self, result):
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_read_only(self):
        self.assertIsNone(guard.evaluate(payload("git status --short")))

    def test_safe_delete_does_not_grant_permissions(self):
        self.assertIsNone(guard.evaluate(payload("rm -rf /tmp/codex-guard-test/build")))

    def test_confirmation_becomes_denial(self):
        result = guard.evaluate(payload("rm -rf /etc/example"))
        self.assert_denied(result)
        self.assertIn("cannot open a confirmation", str(result))

    def test_git_is_left_to_codex(self):
        for command in ["git clean -fdx", "git worktree remove --force /home/u/wt",
                        'git -C "$S/replay" clean -fdq']:
            with self.subTest(command=command):
                self.assertIsNone(guard.evaluate(payload(command)))

    def test_relocated_git_clean(self):
        for command in ["git --work-tree=/ clean -fdx", 'git --work-tr""ee=/ clean -fdx',
                        "git init -q && git clean -fdx"]:
            with self.subTest(command=command):
                self.assert_denied(guard.evaluate(payload(command)))

    def test_catastrophic_and_compound_commands(self):
        for command in ["rm -rf /", "git status && rm -rf /etc/example",
                        "find /etc -delete", "rsync --delete src/ /etc/example/"]:
            with self.subTest(command=command):
                self.assert_denied(guard.evaluate(payload(command)))

    def test_malformed_payload(self):
        for value in ["not json", "null", "{}", payload(42),
                      json.dumps({"tool_input": {"command": "rm -rf x"}, "cwd": ""})]:
            with self.subTest(value=value):
                self.assert_denied(guard.evaluate(value))

    def test_missing_and_broken_classifier(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "classifier.py"
            self.assert_denied(guard.evaluate(payload("git status"), script))
            for body in ["raise RuntimeError('broken')", "print('not json')",
                         "print('{}')"]:
                script.write_text(body)
                self.assert_denied(guard.evaluate(payload("git status"), script))

    def test_timeout(self):
        with patch.object(guard.subprocess, "run", side_effect=subprocess.TimeoutExpired("guard", 8)):
            self.assert_denied(guard.evaluate(payload("git status")))

    def test_shell_wrapper_missing_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / "wrapper.sh"
            wrapper.write_bytes((ADAPTER.parent / "destructive-guard.sh").read_bytes())
            result = subprocess.run(["sh", str(wrapper)], input=payload("git status"),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Command blocked", result.stderr)

    def test_hook_entrypoint(self):
        config = json.loads((ROOT / "config/codex/hooks.json").read_text())
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        import os
        env = {**os.environ, "CODEX_HOME": str(ROOT / "config/codex")}
        result = subprocess.run(["sh", "-c", command], input=payload("rm -rf /etc/example"),
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_denied(json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
