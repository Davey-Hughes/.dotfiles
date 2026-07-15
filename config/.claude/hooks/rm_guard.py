#!/usr/bin/env python3
"""PreToolUse (Bash) guard for recursive rm.

Allowlist, not blocklist: an operand must be *proven* safe or the command falls
through to `ask`. Every parse failure, unknown construct, and unhandled case
lands on `ask`, so a bug here costs a prompt rather than a filesystem.

A previous unattended session ran `rm -rf` with a variable that expanded to /*
and wiped the machine. Note the mechanism carefully -- `set -u`/`setopt nounset`
do NOT prevent it, because a variable that is *set but empty* is not an error:

    W=""; rm -rf "$W"/*   ->   rm -rf /bin /boot /dev /etc /home ...

Quoting does not help either. Only ${W:?} (with the colon) aborts on empty.
The suffix is the whole danger: bare "$W" is harmless when empty, "$W"/* is not.
"""
import json
import os
import posixpath
import re
import shlex
import sys

# --- decisions ---------------------------------------------------------------

def emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)

def passthrough():
    """No recursive rm here; stay silent and let normal permissions apply."""
    sys.exit(0)

# --- policy ------------------------------------------------------------------

# Bare "$VAR" is safe when empty, but says nothing about VAR being *set* to
# something huge. These names are never safe to hand to rm -rf unresolved.
DANGEROUS_VARS = {"HOME", "PWD", "OLDPWD", "ROOT", "USER", "TMPDIR"}

# ${VAR:?...} aborts on unset OR empty. ${VAR?...} (no colon) does NOT catch
# empty and is deliberately excluded.
GUARDED_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:\?[^}]*\}$")
EXPANSION_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?$!-]")
BARE_VAR_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
GLOB_CHARS = "*?["

RM_NAMES = {"rm"}
# Raw-text scan, run BEFORE tokenizing. shlex is precise enough to dismantle
# constructs it does not understand -- `eval "rm -rf $X"` becomes one opaque
# token whose basename is not "rm" -- so a tokenizer-only check silently misses
# them. Hyphens are excluded on both sides so `docker run --rm` does not match.
RM_WORD_RE = re.compile(r"(?<![\w-])rm(?![\w-])")
# Used when the raw text mentions rm but no rm *command* survived parsing. Prose
# ("did my rm get logged?") must not prompt; a real recursive rm hiding inside a
# construct we failed to parse must.
RM_RECURSIVE_RE = re.compile(
    r"(?<![\w-])rm\s+(?:-[a-zA-Z]*[rR]|--recursive|--no-preserve-root)")
SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
REDIRECTS = {">", ">>", "<", "<<", ">&", "<&", "2>", "|&"}
# Constructs we cannot statically reason about.
OPAQUE = {"eval", "exec", "source", "xargs", "find", "."}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}


def safe_roots(cwd):
    roots = ["/tmp", "/var/tmp"]
    for var in ("TMPDIR",):
        v = os.environ.get(var)
        if v:
            roots.append(posixpath.normpath(v))
    if cwd and posixpath.isabs(cwd):
        roots.append(posixpath.normpath(cwd))
    return roots


def classify_path(p, cwd):
    """Classify a fully-literal path. -> (decision, reason)"""
    if not posixpath.isabs(p):
        # A literal relative path cannot become "/" at any cwd. Bounded blast
        # radius, so it does not need cd-tracking to clear.
        if ".." in p.split("/"):
            return "ask", "relative path escapes upward via '..'"
        return "allow", "literal relative path"

    norm = posixpath.normpath(p)
    if norm == "/":
        return "deny", "operand is the filesystem root"
    parts = [x for x in norm.split("/") if x]
    for root in safe_roots(cwd):
        rp = [x for x in posixpath.normpath(root).split("/") if x]
        if parts[:len(rp)] == rp and len(parts) >= len(rp) + 1:
            if len(parts) - len(rp) < 1:
                return "ask", f"too shallow under {root}"
            return "allow", f"literal path under safe root {root}"
    return "ask", f"absolute path outside safe roots: {norm}"


def classify_operand(op, cwd):
    """-> (decision, reason). Anything not provably safe returns ask/deny."""
    if "`" in op or "$(" in op:
        return "ask", "command substitution cannot be statically resolved"

    # Tilde: never resolved here.
    if op == "~" or op == "~/":
        return "deny", "operand is the home directory"
    if op.startswith("~"):
        return "ask", "tilde expansion not statically resolved"

    exps = EXPANSION_RE.findall(op)
    if exps:
        m = BARE_VAR_RE.match(op)
        if m:
            name = m.group(1)
            if name in DANGEROUS_VARS:
                return "deny", f"${name} expands to a critical directory"
            # Bare expansion, no suffix: empty -> `rm -rf ""` (errors),
            # unset+unquoted -> no operands (errors). Nothing to promote to /.
            return "allow", f"bare ${name} with no suffix"
        if all(GUARDED_RE.match(e) for e in exps):
            return "allow", "all expansions are ${VAR:?}-guarded"
        return "ask", (
            "unguarded expansion with a suffix -- if it is empty this becomes "
            "a path under /. Use \"${VAR:?}\" instead."
        )

    if any(c in op for c in GLOB_CHARS):
        # A glob needs a literal prefix to bound it. `rm -rf *` is unbounded:
        # its blast radius is whatever cwd happens to be.
        idx = min((op.index(c) for c in GLOB_CHARS if c in op))
        prefix = op[:idx]
        if "/" not in prefix:
            return "ask", "glob with no literal directory prefix"
        prefix = prefix.rsplit("/", 1)[0]
        if prefix in ("", "/"):
            return "deny", "glob directly under the filesystem root"
        return classify_path(prefix, cwd)

    return classify_path(op, cwd)


def analyse(cmd, cwd):
    # Raw scan first: no mention of rm anywhere means nothing to judge.
    if not RM_WORD_RE.search(cmd):
        return None, ""

    # From here the text mentions rm, so we must reach a real decision. Check
    # the RAW string for constructs tokenizing would destroy the evidence of.
    if "`" in cmd or "$(" in cmd:
        return "ask", "command substitution near an rm cannot be resolved statically"

    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError as e:
        return "ask", f"command could not be parsed ({e})"

    # Opaque constructs bail -- but only in *command position*. `.` is the POSIX
    # source builtin at the start of a segment and a jq filter in `jq -r . f`;
    # matching it positionally is the difference between the two.
    for i, t in enumerate(toks):
        base = os.path.basename(t)
        in_cmd_pos = i == 0 or toks[i - 1] in SEPARATORS
        if in_cmd_pos and base in OPAQUE:
            return "ask", f"command contains `{base}`, which hides its arguments"
        if base in SHELLS and i + 1 < len(toks) and toks[i + 1] == "-c":
            return "ask", f"command contains `{base} -c`, which hides its arguments"
    if "--no-preserve-root" in toks:
        return "deny", "--no-preserve-root defeats rm's own safety net"

    worst, reason = None, ""
    found_rm = False
    rank = {"allow": 0, "ask": 1, "deny": 2}
    i = 0
    while i < len(toks):
        if os.path.basename(toks[i]) not in RM_NAMES:
            i += 1
            continue
        found_rm = True
        i += 1
        recursive, operands = False, []
        while i < len(toks) and toks[i] not in SEPARATORS:
            t = toks[i]
            if t in REDIRECTS:
                i += 2  # skip redirect target
                if operands and operands[-1].isdigit():
                    operands.pop()  # that was an fd, not an operand
                continue
            if t.startswith("--"):
                if t in ("--recursive", "--dir"):
                    recursive = True
            elif t.startswith("-") and len(t) > 1:
                if any(c in t for c in "rR"):
                    recursive = True
            else:
                operands.append(t)
            i += 1
        if not recursive:
            continue
        if not operands:
            continue
        for op in operands:
            d, r = classify_operand(op, cwd)
            if worst is None or rank[d] > rank[worst]:
                worst, reason = d, f"`{op}`: {r}"

    if not found_rm:
        # No rm command survived parsing. Every construct that could execute a
        # string (eval, <shell> -c, xargs, find, substitution) already bailed
        # above, so a bare mention here is inert text -- unless it actually
        # reads as a recursive rm, in which case we failed to parse something
        # real and must not approve it.
        if RM_RECURSIVE_RE.search(cmd):
            return "ask", "text reads as a recursive rm the guard could not parse"
        return None, ""
    return worst, reason


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        emit("ask", f"guard could not read hook payload ({e}); asking to be safe")
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or ""
    if not cmd:
        passthrough()
    try:
        decision, reason = analyse(cmd, cwd)
    except Exception as e:
        emit("ask", f"guard errored ({type(e).__name__}: {e}); asking to be safe")
    if decision is None:
        passthrough()
    if decision == "allow":
        emit("allow", f"recursive rm on a provably safe target -- {reason}")
    if decision == "deny":
        emit("deny", f"refusing catastrophic rm -- {reason}")
    emit("ask", f"recursive rm needs confirmation -- {reason}")


if __name__ == "__main__":
    main()
