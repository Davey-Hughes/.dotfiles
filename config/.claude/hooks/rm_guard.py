#!/usr/bin/env python3
"""PreToolUse (Bash) guard for destructive recursive commands.

Scope is deliberately narrow: operations that *descend* and destroy. Anything
that cannot recurse is out of scope, because it cannot produce the failure this
guard exists to prevent -- `rm x` names one file no matter what x is.

Covered, each with its own notion of "which argument is the target":

    rm -r/-R                all operands
    find ... -delete        the search roots
    find ... -exec rm       the search roots
    fd -x/-X rm             the search roots (fd's --exec is find's -exec)
    rsync --delete          the DESTINATION (last operand)
    rclone sync/purge/...   the destination / the named remote path
    git clean -f            the working tree
    ssh host '<cmd>'        re-analysed remotely, with no local safe roots

Deliberately NOT covered:

    non-recursive rm    without -r, rm cannot descend into a directory.
    git rm              a working-tree operation: only touches tracked files,
                        never untracked data, all of it recoverable from history.
                        `git clean` is the opposite and IS covered.
    rg                  read-only. `-x` is --line-regexp and `-r` is --replace
                        (rewrites printed output, never the file). A rule here
                        would only collide with fd's exec flags and misfire.
    dd, diskutil        catastrophic but device-shaped, not path-shaped. They
                        need a different check than a path classifier.

Within that scope it is an allowlist, not a blocklist: an operand must be
*proven* safe or the command falls through to `ask`. Every parse failure,
unknown construct, and unhandled case lands on `ask`, so a bug here costs a
prompt rather than a filesystem.

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
    """Nothing destructive here; stay silent and let normal permissions apply."""
    sys.exit(0)

# --- policy ------------------------------------------------------------------

# Bare "$VAR" is safe when empty, but says nothing about VAR being *set* to
# something huge. These names are never safe to hand to a recursive delete.
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
# `git rm` and its option-bearing forms (`git -C /path rm`). The token class
# excludes the shell separators on purpose: without that, `git status && rm -rf
# /` would match end to end and the real rm would be blanked out with it.
GIT_RM_RE = re.compile(
    r"(?<![\w-])git(?![\w-])(?:[ \t]+[-\w./=~:@]+){0,8}?[ \t]+rm(?![\w-])")
# Every command name this guard knows how to reason about. No match anywhere in
# the text means there is nothing here to judge, and we stop immediately.
GUARDED_CMD_RE = re.compile(
    r"(?<![\w-])(?:rm|find|fd|fdfind|rsync|rclone|git|ssh)(?![\w-])")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
REDIRECTS = {">", ">>", "<", "<<", ">&", "<&", "2>", "|&"}
# Constructs we cannot statically reason about.
OPAQUE = {"eval", "exec", "source", "xargs", "."}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
GIT_NAMES = {"git"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                  "--exec-path", "--config-env"}
# Words that prefix a command without changing which command it is.
WRAPPERS = {"sudo", "command", "env", "nohup", "time", "nice"}

# Commands that, handed to find -exec / fd -x, destroy what they are given.
DESTRUCTIVE_EXEC = {"rm", "rmdir", "unlink", "shred", "trash", "truncate", "dd"}
FIND_LEADING_OPTS = {"-H", "-L", "-P", "-E", "-s", "-d", "-x"}
FIND_EXEC_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}
FD_EXEC_FLAGS = {"-x", "--exec", "-X", "--exec-batch"}
# fd options that consume a following value, so their value is not a search path.
FD_VALUE_OPTS = {
    "-d", "--max-depth", "--min-depth", "--exact-depth", "-t", "--type",
    "-e", "--extension", "-E", "--exclude", "-S", "--size", "--changed-within",
    "--changed-before", "--owner", "--base-directory", "--path-separator",
    "--search-path", "--threads", "-j", "--max-results", "--color",
    "--ignore-file", "--format", "--and", "--strip-cwd-prefix",
}
# rsync options that consume a following value. Needed so `--exclude foo` is not
# mistaken for the destination operand.
RSYNC_VALUE_OPTS = {
    "--exclude", "--include", "--filter", "-f", "--files-from", "--exclude-from",
    "--include-from", "--log-file", "--log-file-format", "--temp-dir", "-T",
    "--backup-dir", "--suffix", "--compare-dest", "--copy-dest", "--link-dest",
    "-e", "--rsh", "--rsync-path", "--port", "--timeout", "--contimeout",
    "--bwlimit", "--chmod", "--chown", "--usermap", "--groupmap", "--out-format",
    "--partial-dir", "--sockopts", "--password-file", "--write-batch",
    "--only-write-batch", "--read-batch", "--protocol", "--iconv", "--max-size",
    "--min-size", "--max-delete", "--modify-window", "-B", "--block-size",
    "--checksum-seed", "--info", "--debug", "-M", "--remote-option",
    "--skip-compress", "--compress-level", "--address", "--sockopts",
}
# Short flags whose value is the next token when they end a bundle (-e ssh).
RSYNC_SHORT_VALUE = set("efTBM")
RCLONE_DESTRUCTIVE = {
    "sync", "purge", "delete", "deletefile", "rmdir", "rmdirs", "move",
    "cleanup",
}
SSH_VALUE_OPTS = {
    "-p", "-i", "-l", "-o", "-F", "-L", "-R", "-D", "-c", "-m", "-b", "-e",
    "-J", "-W", "-S", "-w", "-Q", "-E", "-I", "-B",
}
# A remote spec: user@host:path, host:path, or a URL. Never a local path.
REMOTE_SPEC_RE = re.compile(r"^[A-Za-z0-9_.+-]*(?:@[A-Za-z0-9_.-]+)?:|^[a-z][a-z0-9+.-]*://")

RANK = {"allow": 0, "ask": 1, "deny": 2}
MAX_SSH_DEPTH = 3


# Gates the conservative bail-outs on recursion actually being in play. Matched
# crudely -- any short flag containing r/R, so `jq -r` counts -- because a false
# positive here only costs a prompt.
RECURSIVE_FLAG_RE = re.compile(
    r"(?<![\w-])(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive|--no-preserve-root)(?![\w-])")
FIND_WORD_RE = re.compile(r"(?<![\w-])find(?![\w-])")
FIND_ACTION_RE = re.compile(r"(?<![\w-])-(?:delete|exec|execdir|ok|okdir)(?![\w-])")
FD_WORD_RE = re.compile(r"(?<![\w-])fd(?:find)?(?![\w-])")
FD_EXEC_RE = re.compile(r"(?<![\w-])(?:-x|-X|--exec|--exec-batch)(?![\w-])")
RSYNC_WORD_RE = re.compile(r"(?<![\w-])rsync(?![\w-])")
RSYNC_DELETE_RE = re.compile(r"(?<![\w-])--del(?:ete)?(?:-[a-z]+)?(?![\w-])")
RCLONE_WORD_RE = re.compile(r"(?<![\w-])rclone(?![\w-])")
RCLONE_ACTION_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(sorted(RCLONE_DESTRUCTIVE, key=len, reverse=True)) + r")(?![\w-])")
GIT_WORD_RE = re.compile(r"(?<![\w-])git(?![\w-])")
GIT_CLEAN_RE = re.compile(r"(?<![\w-])clean(?![\w-])")

# Each pair is (command name, the flag that makes it destructive). Both must
# appear before the conservative bail-outs fire, so `git diff -r $(...)` -- a
# guarded name and a recursion-shaped flag, but no destructive pairing between
# them -- does not prompt.
DANGER_PAIRS = (
    (RM_WORD_RE, RECURSIVE_FLAG_RE),
    (FIND_WORD_RE, FIND_ACTION_RE),
    (FD_WORD_RE, FD_EXEC_RE),
    (RSYNC_WORD_RE, RSYNC_DELETE_RE),
    (RCLONE_WORD_RE, RCLONE_ACTION_RE),
    (GIT_WORD_RE, GIT_CLEAN_RE),
)


def danger_hint(text):
    """Does the raw text plausibly contain a destructive invocation?"""
    return any(name.search(text) and flag.search(text)
               for name, flag in DANGER_PAIRS)


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
        # ...with one exception: `.` IS cwd, and `./x` is one level under it.
        # Harmless in a project directory, catastrophic at / or $HOME. cwd is in
        # the hook payload, so resolve and check that single case.
        if cwd and posixpath.isabs(cwd):
            resolved = posixpath.normpath(posixpath.join(cwd, p))
            home = os.environ.get("HOME", "")
            depth = len([x for x in resolved.split("/") if x])
            if resolved == "/" or depth <= 1:
                return "deny", f"relative path resolves to {resolved}"
            if home and resolved == posixpath.normpath(home):
                return "deny", "relative path resolves to the home directory"
            return "allow", "literal relative path"
        # No cwd to resolve against -- this is the far side of an ssh. A named
        # subdirectory is still bounded, but an operand that IS the working
        # directory is whatever the remote login lands in, usually $HOME.
        if posixpath.normpath(p) == ".":
            return "ask", "operand is the working directory, unknown on the far end"
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


def classify_operand(op, cwd, bare_var_ok=True):
    """-> (decision, reason). Anything not provably safe returns ask/deny.

    bare_var_ok encodes an rm-specific fact: `rm -rf $X` with X unset or empty
    is an *error*, not a catastrophe, so a bare expansion is safe there. It is
    not safe for commands that treat "no path" as "use the current directory" --
    `find $X -delete` with X unset becomes `find -delete`, which is `rm -rf .`.
    """
    if "`" in op or "$(" in op:
        return "ask", "command substitution cannot be statically resolved"

    if REMOTE_SPEC_RE.match(op):
        return "ask", "remote path -- the guard cannot see what is on the far end"

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
            if not bare_var_ok:
                return "ask", (
                    f"bare ${name} -- if it is unset this command falls back to "
                    f"the current directory. Use \"${{{name}:?}}\" instead."
                )
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


# --- segmentation ------------------------------------------------------------

def segment_bounds(toks):
    """-> [(start, end)] index pairs, one per pipeline/list segment."""
    bounds, start, n = [], 0, len(toks)
    while start < n:
        end = start
        while end < n and toks[end] not in SEPARATORS:
            end += 1
        if end > start:
            bounds.append((start, end))
        start = end + 1
    return bounds


def command_word(argv):
    """-> (index, basename) of the real command, past wrappers and VAR=val."""
    j = 0
    while j < len(argv) and (ENV_ASSIGN_RE.match(argv[j])
                             or os.path.basename(argv[j]) in WRAPPERS):
        j += 1
    if j >= len(argv):
        return j, ""
    return j, os.path.basename(argv[j])


def git_segments(toks):
    """-> one bool per token: is this token part of a `git ...` invocation?

    Segment-level rather than "the word before rm is git", so `git -C /path rm`
    and `sudo git rm` are both recognised while `git status && rm -rf /` keeps
    its second segment fully guarded.
    """
    flags = [False] * len(toks)
    for start, end in segment_bounds(toks):
        _, base = command_word(toks[start:end])
        if base in GIT_NAMES:
            for k in range(start, end):
                flags[k] = True
    return flags


# --- per-command target extraction -------------------------------------------

def find_targets(argv):
    """find: -> (destructive, roots). Roots are the paths find descends from."""
    i = 1
    while i < len(argv) and argv[i] in FIND_LEADING_OPTS:
        i += 1
    roots = []
    while i < len(argv) and not argv[i].startswith("-") and argv[i] not in ("(", "!", ")"):
        roots.append(argv[i])
        i += 1
    destructive = False
    rest = argv[i:]
    for j, t in enumerate(rest):
        if t == "-delete":
            destructive = True
        elif t in FIND_EXEC_ACTIONS and j + 1 < len(rest):
            if os.path.basename(rest[j + 1]) in DESTRUCTIVE_EXEC:
                destructive = True
    return destructive, roots


def fd_targets(argv):
    """fd: -> (destructive, roots). fd's --exec is find's -exec."""
    positional, destructive = [], False
    i = 1
    while i < len(argv):
        t = argv[i]
        if t in FD_EXEC_FLAGS:
            if i + 1 < len(argv) and os.path.basename(argv[i + 1]) in DESTRUCTIVE_EXEC:
                destructive = True
            break  # everything after this is the exec'd command, not a path
        if t.startswith("-"):
            if t in FD_VALUE_OPTS and "=" not in t:
                i += 2
                continue
            i += 1
            continue
        positional.append(t)
        i += 1
    # fd [pattern] [path...] -- the first positional is the pattern.
    roots = positional[1:]
    return destructive, roots


def rsync_targets(argv):
    """rsync: -> (destructive, [destination]). The destination is what --delete
    prunes to match the source, so it is the only operand at risk."""
    destructive, operands, ok = False, [], True
    i = 1
    while i < len(argv):
        t = argv[i]
        if t == "--":
            operands.extend(argv[i + 1:])
            break
        if t.startswith("--"):
            head = t.split("=", 1)[0]
            if RSYNC_DELETE_RE.match(head):
                destructive = True
            if "=" not in t and head in RSYNC_VALUE_OPTS:
                i += 2
                continue
        elif t.startswith("-") and len(t) > 1:
            if t[-1] in RSYNC_SHORT_VALUE:
                i += 2
                continue
        else:
            operands.append(t)
        i += 1
    if len(operands) < 2:
        ok = False  # cannot tell which operand is the destination
    return destructive, (operands[-1:] if ok else []), ok


def rclone_targets(argv):
    """rclone: -> (destructive, targets). sync/move destroy the destination;
    purge/delete/rmdir destroy what they name."""
    operands, sub = [], ""
    i = 1
    while i < len(argv):
        t = argv[i]
        if t.startswith("-"):
            i += 2 if ("=" not in t and t in ("--config", "--log-file", "--filter",
                                              "--exclude", "--include", "--transfers",
                                              "--checkers", "--bwlimit")) else 1
            continue
        operands.append(t)
        i += 1
    if not operands:
        return False, []
    sub = operands[0]
    if sub not in RCLONE_DESTRUCTIVE:
        return False, []
    rest = operands[1:]
    if sub in ("sync", "move"):
        return True, rest[-1:]      # destination is pruned to match source
    return True, rest               # purge/delete/rmdir destroy what they name


def git_clean_targets(argv):
    """git clean -f: -> (destructive, targets). Removes untracked and (with -x)
    ignored files. Unlike `git rm`, none of it is recoverable from history."""
    idx, _ = command_word(argv)
    i = idx + 1
    while i < len(argv) and argv[i].startswith("-"):
        # git's own options, some of which eat the next token (`git -C /path`).
        if argv[i] in GIT_VALUE_OPTS and "=" not in argv[i]:
            i += 2
            continue
        i += 1
    if i >= len(argv) or argv[i] != "clean":
        return False, []
    forced, paths = False, []
    for t in argv[i + 1:]:
        if t.startswith("--"):
            if t == "--force":
                forced = True
        elif t.startswith("-") and len(t) > 1:
            if "f" in t:
                forced = True
        else:
            paths.append(t)
    if not forced:
        return False, []  # git clean refuses to do anything without -f
    return True, paths or ["."]


def ssh_remote_command(argv):
    """ssh: -> the remote command string, or "" if this is an interactive login."""
    idx, _ = command_word(argv)
    i = idx + 1
    while i < len(argv):
        t = argv[i]
        if t.startswith("-"):
            if t in SSH_VALUE_OPTS and len(t) == 2:
                i += 2
                continue
            i += 1
            continue
        break
    if i >= len(argv):
        return ""
    rest = argv[i + 1:]  # argv[i] is the host
    if not rest:
        return ""
    return rest[0] if len(rest) == 1 else " ".join(rest)


# --- analysis ----------------------------------------------------------------

def analyse_segment(argv, cwd, depth):
    """-> [(decision, reason)] for the non-rm commands in one segment."""
    out = []
    idx, base = command_word(argv)
    argv = argv[idx:]
    if not argv:
        return out

    def rate(targets, label, bare_var_ok=False):
        if not targets:
            out.append(("ask", f"{label} with no explicit path -- defaults to the "
                               f"current directory"))
            return
        for t in targets:
            d, r = classify_operand(t, cwd, bare_var_ok=bare_var_ok)
            out.append((d, f"{label} `{t}`: {r}"))

    if base == "find":
        destructive, roots = find_targets(argv)
        if destructive:
            rate(roots, "find deletes under")
    elif base in ("fd", "fdfind"):
        destructive, roots = fd_targets(argv)
        if destructive:
            # fd with no path argument searches the current directory, exactly
            # as `find . ` does -- so treat it as the literal `.` rather than as
            # a missing operand.
            rate(roots or ["."], "fd deletes under")
    elif base == "rsync":
        destructive, dest, ok = rsync_targets(argv)
        if destructive and not ok:
            out.append(("ask", "rsync --delete but the destination operand could "
                               "not be identified"))
        elif destructive:
            rate(dest, "rsync --delete prunes destination")
    elif base == "rclone":
        destructive, targets = rclone_targets(argv)
        if destructive:
            rate(targets, "rclone destroys")
    elif base in GIT_NAMES:
        destructive, paths = git_clean_targets(argv)
        if destructive:
            for t in paths:
                d, r = classify_operand(t, cwd, bare_var_ok=False)
                # Untracked and ignored files are not in history. Even a "safe"
                # path only earns a prompt, never a silent pass.
                out.append(("ask" if d == "allow" else d,
                            f"git clean removes untracked/ignored files under `{t}`: {r}"))
    elif base == "ssh":
        remote = ssh_remote_command(argv)
        if remote and depth < MAX_SSH_DEPTH:
            # No local cwd applies on the far end, so pass cwd="": only /tmp and
            # /var/tmp stay safe and every other absolute path asks.
            d, r = analyse(remote, "", depth + 1)
            if d is not None:
                out.append((d, f"over ssh: {r}"))
        elif remote:
            out.append(("ask", "ssh nested too deeply to analyse"))
    return out


def analyse(cmd, cwd, depth=0):
    # Raw scan first: no guarded command name anywhere means nothing to judge.
    if not GUARDED_CMD_RE.search(cmd):
        return None, ""

    # Blank out `git rm`, then look again. If nothing guarded survives, the
    # command is git's business and none of ours. Done on the raw text so the
    # heuristics below never see a git rm either.
    text = GIT_RM_RE.sub(" git-rm ", cmd)
    if not GUARDED_CMD_RE.search(text):
        return None, ""

    # The bail-outs below exist to stop a *destructive* command hiding inside
    # something unreadable. With no destructive pairing anywhere -- no recursion
    # flag next to an rm, no -delete next to a find -- there is nothing to hide,
    # so `ls | xargs rm` goes straight to the token scan instead of prompting.
    danger = danger_hint(text)

    # Check the RAW string for constructs tokenizing would destroy the evidence of.
    if danger and ("`" in text or "$(" in text):
        return "ask", "command substitution near a destructive command cannot be resolved statically"

    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError as e:
        return "ask", f"command could not be parsed ({e})"

    # Opaque constructs bail -- but only in *command position*. `.` is the POSIX
    # source builtin at the start of a segment and a jq filter in `jq -r . f`;
    # matching it positionally is the difference between the two.
    if danger:
        for i, t in enumerate(toks):
            base = os.path.basename(t)
            in_cmd_pos = i == 0 or toks[i - 1] in SEPARATORS
            if in_cmd_pos and base in OPAQUE:
                return "ask", f"command contains `{base}`, which hides its arguments"
            if base in SHELLS and i + 1 < len(toks) and toks[i + 1] == "-c":
                return "ask", f"command contains `{base} -c`, which hides its arguments"
    if "--no-preserve-root" in toks:
        return "deny", "--no-preserve-root defeats rm's own safety net"

    in_git = git_segments(toks)
    worst, reason = None, ""

    def note(d, r):
        nonlocal worst, reason
        if worst is None or RANK[d] > RANK[worst]:
            worst, reason = d, r

    # --- rm itself. Scanned across all tokens, not just command position, so
    # `sudo rm -rf` and `xargs rm -r` are both seen.
    found_rm = False
    i = 0
    while i < len(toks):
        if os.path.basename(toks[i]) not in RM_NAMES or in_git[i]:
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
            note(d, f"`{op}`: {r}")

    # --- everything else, one segment at a time.
    for start, end in segment_bounds(toks):
        for d, r in analyse_segment(toks[start:end], cwd, depth):
            note(d, r)

    if not found_rm and worst is None:
        # Nothing survived parsing. Every construct that could execute a string
        # (eval, <shell> -c, xargs, substitution) already bailed above, so a bare
        # mention here is inert text -- unless it actually reads as a recursive
        # rm, in which case we failed to parse something real and must not
        # approve it. Searched against the git-stripped text, so an exempted
        # `git -C /path rm -r` does not read as unparsed.
        if RM_RECURSIVE_RE.search(text):
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
        emit("allow", f"destructive command on a provably safe target -- {reason}")
    if decision == "deny":
        emit("deny", f"refusing catastrophic delete -- {reason}")
    emit("ask", f"destructive recursive command needs confirmation -- {reason}")


if __name__ == "__main__":
    main()
