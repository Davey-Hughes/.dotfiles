#!/usr/bin/env python3
"""Translate the shared Claude classifier's verdicts to supported Codex output."""
import json
from pathlib import Path
import subprocess
import sys


GUARD = Path(__file__).resolve().parents[3] / "config/.claude/hooks/rm_guard.py"


def deny(reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def evaluate(payload, guard=GUARD):
    try:
        event = json.loads(payload)
        command = event["tool_input"]["command"]
        cwd = event["cwd"]
        if not isinstance(command, str) or not command.strip():
            raise ValueError("missing shell command")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise ValueError("missing absolute working directory")
        result = subprocess.run(
            [sys.executable, str(guard)], input=payload,
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode:
            raise ValueError(f"classifier exited {result.returncode}")
        if not result.stdout.strip():
            return None
        verdict = json.loads(result.stdout)["hookSpecificOutput"]
        decision = verdict["permissionDecision"]
        reason = verdict["permissionDecisionReason"]
        if not isinstance(reason, str):
            raise ValueError("invalid classifier reason")
        if decision == "allow":
            # Leave Codex's permissions in control; never approve the whole call.
            return None
        if decision == "deny":
            return deny(reason)
        if decision == "ask":
            return deny(
                reason + "\nCodex cannot open a confirmation prompt from this hook. "
                "Stop and explain the command and targets to the user. "
                "Do not retry through another tool, escalation, or rewritten command. "
                "The user can inspect and run the command manually outside Codex."
            )
        raise ValueError("unknown classifier decision")
    except Exception as exc:
        return deny(f"Destructive-command guard could not check this call "
                    f"({type(exc).__name__}: {exc}). Command blocked; repair the guard.")


if __name__ == "__main__":
    output = evaluate(sys.stdin.read())
    if output is not None:
        print(json.dumps(output))
