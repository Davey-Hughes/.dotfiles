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

Command substitution is masked before tokenizing rather than bailed out on.
shlex with punctuation_chars=True splits on `(` and `)`, which shatters an
UNQUOTED substitution into pieces that each classify as a harmless literal:

    rm -rf $(cat list)  ->  ['rm', '-rf', '$', '(', 'cat', 'list', ')']

Four of those are operands as far as the scanner can tell, all of them relative
paths, so the whole command comes back *allow* -- worse than no guard at all.
Quoted `"$(pwd)/x"` and backticks survive shlex intact and were never the
problem; the unquoted form is. Masking each span to a sentinel word keeps it
whole AND keeps it glued to its neighbours, which naive re-joining cannot do:
`$(pwd)/b` must stay one operand, because `/b` on its own reads as an absolute
path that is nothing like what runs.

Two things a substitution can hide that no operand check would catch, so both
are refused outright:

    $(...) in command position     which command runs is unknowable
    $(...) among an rm's operands  it may expand to -rf, so recursion is assumed

The second costs a prompt on `rm $(ls *.tmp)`, which cannot descend. That is
the allowlist doing its job: unprovable reads as unsafe.

Output shape: every dangerous operand becomes a Finding, and the verdict names
the segment it came from rather than the whole command line, so `deploy.sh &&
rm -rf $W/*` reports the `rm` half and not the deploy. Bail-outs echo their
segment too -- `xargs` alone says which word stopped the guard and nothing about
what xargs was told to do, so what it hides is echoed and lit up with it.

Claude Code renders permissionDecisionReason through an ANSI-aware component, so
the colours below survive into the permission dialog: the command bold red, what
is at risk bold yellow, anything the guard cannot resolve bold magenta, and the
${VAR:?} it wants you to write instead bold cyan.
"""
import json
import os
import posixpath
import re
import shlex
import sys
from collections import namedtuple
from typing import NoReturn

# --- decisions ---------------------------------------------------------------
# Both of these exit. Annotated NoReturn so the bail-out arms in main() read as
# terminal rather than as fallthrough.

def emit(decision, reason) -> NoReturn:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)

def passthrough() -> NoReturn:
    """Nothing destructive here; stay silent and let normal permissions apply."""
    sys.exit(0)

# One dangerous operand, and enough context to point at it.
#
#   decision  allow / ask / deny for this operand alone
#   segment   tokens of the pipeline segment it came from, echoed back so the
#             verdict names the offending command and not the whole line
#   label     why THIS operand is the one at risk ("rsync --delete prunes the
#             destination"). None for rm, where the operands speak for themselves
#   operand   the flagged token, or None for a whole-segment complaint
#   reason    the explanation from classify_operand
#   highlight extra tokens to light up in the echo that are NOT operands and get
#             no row of their own -- the arguments of a construct that hides what
#             it runs, where the complaint is the whole invocation and not one
#             token in it
Finding = namedtuple("Finding", "decision segment label operand reason highlight",
                     defaults=((),))


def bare(decision, reason):
    """A verdict with no operand to point at -- a parse failure or a bail-out."""
    return Finding(decision, None, None, None, reason)


# Every other verdict gets its emphasis from the echoed command. A bail-out has
# no command to echo -- it is one sentence, and "command contains xargs, which
# hides its arguments" buries the only word that explains it mid-sentence. So a
# reason marks that span, and the renderer paints what is marked.
#
# A private-use character rather than backticks, because a marked span is often a
# fragment of the command itself and `pwd` -rf / puts backticks in that fragment.
# Stripped from the input alongside SENTINEL, so nothing a user types can forge
# a mark or split one in half.
MARK = "\ue001"


def mark(s):
    return f"{MARK}{s}{MARK}"

# --- policy ------------------------------------------------------------------

# Bare "$VAR" is safe when empty, but says nothing about VAR being *set* to
# something huge. These names are never safe to hand to a recursive delete.
DANGEROUS_VARS = {"HOME", "PWD", "OLDPWD", "ROOT", "USER", "TMPDIR"}

# ${VAR:?...} aborts on unset OR empty. ${VAR?...} (no colon) does NOT catch
# empty and is deliberately excluded.
GUARDED_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:\?[^}]*\}$")
EXPANSION_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?$!-]")
BARE_VAR_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
# The variable name inside any expansion form: $W, ${W}, ${W:-default}.
EXP_NAME_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)")
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
# The trailing [a-zA-Z]* changes nothing about *whether* this matches -- the
# [rR] before it already decided that. It is there so the match is the whole
# flag, because the verdict echoes it back and `rm -r` is not what was written.
RM_RECURSIVE_RE = re.compile(
    r"(?<![\w-])rm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|--no-preserve-root)")
# `git rm` and its option-bearing forms (`git -C /path rm`). The token class
# excludes the shell separators on purpose: without that, `git status && rm -rf
# /` would match end to end and the real rm would be blanked out with it.
GIT_RM_RE = re.compile(
    r"(?<![\w-])git(?![\w-])(?:[ \t]+[-\w./=~:@]+){0,8}?[ \t]+rm(?![\w-])")
# Every command name this guard knows how to reason about. No match anywhere in
# the text means there is nothing here to judge, and we stop immediately.
GUARDED_CMD_RE = re.compile(
    r"(?<![\w-])(?:rm|find|fd|fdfind|rsync|rclone|git|ssh)(?![\w-])")

# --- text the shell never executes -------------------------------------------
# The raw-text heuristics below read the command as code. Two regions of a
# command line are not code, and reading them as code is how this guard spent
# its first week prompting on `git commit`: a commit message *about* shell
# commands is indistinguishable from shell commands.
#
#     git commit -m "$(cat <<'EOF'
#     fix(claude): collapse duplicate findings
#
#     `git clean -fdxn | head` is two segments that say the same thing.
#     EOF
#     )"
#
# Nothing there deletes anything, but it carries a `$(`, the word `git` and the
# word `clean`, which is a destructive pairing as far as danger_hint can tell.
# Worse, prose puts stray quotes in the line (`the "clean flag`), which bash
# takes literally inside a quoted heredoc and shlex chokes on.
#
# A quoted heredoc body (<<'EOF' / <<"EOF") is literal: the shell performs no
# expansion and no substitution inside it. An UNQUOTED <<EOF *does* expand, so
# `$(rm -rf /)` in that body really runs -- those are deliberately left alone.
#
# The terminator rule is bash's, exactly: it must sit at column 0, and only the
# <<- form tolerates anything before it, and then only TABS. Being loose here is
# not a small error -- an indented `EOF` inside the body would end the match
# early and spill the rest of the message back into the scan, which is precisely
# what a commit message quoting a heredoc does.
HEREDOC_RE = re.compile(
    r"<<(-)?[ \t]*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2.*?^(?(1)\t*)\3$",
    re.S | re.M)
# Flags whose argument is prose written for a human. Matched only when the value
# is quoted, so the substitution is bounded by the quote the shell itself uses.
# The residual hole is `--body "$(rm -rf /)"` -- a substitution smuggled into a
# message argument, which does execute and is no longer read. That is a way to
# type a deletion, not a way to make one by accident, and the accident is what
# this guard is for.
MSG_FLAG_RE = re.compile(
    r"(?<![\w-])(?:-m|--message|--body|--title|--description|--notes|--subject)"
    r"(?:[ \t]+|=)"
    r"(?:\$'(?:\\.|[^'\\])*'|'[^']*'|\"(?:\\.|[^\"\\])*\")")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# "\n" is listed for completeness and matches nothing by itself: shlex consumes a
# newline as whitespace, so no token is ever equal to one. mask_opaque normalises
# unquoted newlines to ";" before tokenizing, and that is what actually makes a
# statement on the next line its own segment. Without it, `git commit -m x` and
# an `rm -rf /` on the line below were ONE segment, git_segments() read the whole
# thing as git's business, and the rm was never scanned.
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

# Claude Code parses permissionDecisionReason for SGR sequences and re-renders
# them as styled spans, so these survive into the permission dialog. They also
# ride along into the model's blocking error on a deny, which costs a handful of
# tokens and is worth it to make the flagged operand unmissable.
#
# Three roles, so the echoed command splits the way it reads: what is doing the
# destroying, what is being destroyed, and what is merely along for the ride.
#
# CONTEXT is deliberately empty. Dimming the third role was the obvious choice
# and the wrong one: SGR 2 renders anywhere from "slightly grey" to "barely
# there" depending on the colorscheme, and the parts it covered -- the label
# line and the unflagged operands -- are exactly what you read to work out what
# ELSE is on the line. Red and yellow carry the emphasis on their own.
RED = "\x1b[1;31m"     # the command and its flags -- `rm -rf`, `git clean -fdx`
YELLOW = "\x1b[1;33m"  # the operands that tripped the guard
# A fourth role, painted INSIDE an operand rather than over a whole token. In
# `$(cat list)/build` and in `$W/*` the two halves fail differently -- the
# literal one is readable and the other is not -- and one flat yellow says only
# "something here is wrong". Magenta marks the span whose value the guard
# genuinely cannot know; see unknown_spans for which halves qualify.
UNKNOWN = "\x1b[1;35m"
# ...and a fifth, for the one span in the whole verdict that is not a complaint.
# A reason that ends "Use "${W:?}" instead" is the only thing here you can act
# on, and it sits at the end of the longest line on screen. Cyan rather than
# green: green reads as "this is fine" in a dialog whose entire job is to say
# that it is not, and it is the one hue that survives red/green colourblindness
# without colliding with a role that means danger.
FIX = "\x1b[1;36m"
CONTEXT = ""           # everything else: left at the terminal's normal weight
RESET = "\x1b[0m"
MAX_ECHO = 160       # past this the echoed command elides its unflagged operands
MAX_TOKEN = 120      # ...and any single token is truncated regardless

# The second word is part of the command name, not an operand, for the few
# guarded tools that are subcommand-shaped. Without this `git clean -fdx` would
# render with `clean` dimmed between two red tokens.
SUBCOMMANDS = {"git": {"clean"}, "rclone": RCLONE_DESTRUCTIVE}


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


def strip_data(text):
    """Blank the regions of a command line the shell treats as data, not code.

    Only the raw-text heuristics read this. Tokenizing runs on the original
    command, so the per-command analysis below still sees a message flag's
    value -- mask_opaque covers the one region that has to be hidden from the
    tokenizer too, because prose in a heredoc body breaks shlex outright.
    """
    text = HEREDOC_RE.sub(" HEREDOC ", text)
    return MSG_FLAG_RE.sub(" MESSAGE ", text)


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

    # A masked heredoc body. Unreachable from any sensible command -- a heredoc is
    # a redirect, not a path -- but the sentinel word would otherwise read as a
    # plain relative operand and classify as *allow*, and the rule in this file
    # is that anything not proven safe asks.
    if SENTINEL in op:
        return "ask", "operand is a heredoc body, which is data and not a path"

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
        # Name the actual variables in the fix, not a placeholder -- the advice
        # is only actionable if it can be pasted.
        names = dict.fromkeys(m.group(1) for m in
                              (EXP_NAME_RE.match(e) for e in exps) if m)
        hint = ", ".join('"${%s:?}"' % n for n in names) or '"${VAR:?}"'
        return "ask", (
            "unguarded expansion with a suffix -- if it is empty this becomes "
            f"a path under /. Use {hint} instead."
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


# --- spans the tokenizer must not read ---------------------------------------
# Found on the raw text, where the shell's own quoting rules still apply, and
# replaced with a sentinel word so shlex cannot take the span apart. U+E000 is
# private-use: it has no meaning to any shell and no legitimate command contains
# it, but the input is stripped of it anyway rather than trusted.

SENTINEL = "\ue000"


def _close_paren(s, start):
    """Index of the `)` matching the `(` at s[start], or None if unbalanced.

    Quote-aware, because a `)` inside a quoted string closes nothing -- which is
    the difference between masking `$(cat <<'EOF' ... fix(claude): x ... EOF )`
    correctly and swallowing the rest of the command line.
    """
    depth, quote, i, n = 0, None, start, len(s)
    while i < n:
        c = s[i]
        if quote == "'":
            if c == "'":
                quote = None
        elif c == "\\" and i + 1 < n:
            i += 2
            continue
        elif quote == '"':
            if c == '"':
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _close_backtick(s, start):
    """Index of the backtick closing the one at s[start]. Backticks do not nest."""
    i = start + 1
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 2
            continue
        if s[i] == "`":
            return i
        i += 1
    return None


def subst_spans(s):
    """-> [(start, end)] of every substitution in s, outermost only.

    Used both to mask (on the whole command) and to paint (on one token).
    """
    spans, i, n = [], 0, len(s)
    while i < n:
        end = None
        if s[i] == "$" and i + 1 < n and s[i + 1] == "(":
            end = _close_paren(s, i + 1)
        elif s[i] == "`":
            end = _close_backtick(s, i)
        if end is not None:
            spans.append((i, end + 1))
            i = end + 1
            continue
        i += 1
    return spans


def mask_opaque(cmd):
    """-> (masked, {sentinel: original}). Unbalanced spans are left alone.

    Two kinds of span must not reach shlex, for the same underlying reason:
    neither is shell that a tokenizer can read.

    A QUOTED heredoc body is data -- the shell expands nothing inside <<'EOF' --
    and what goes in one is prose. Prose carries apostrophes, and shlex reads
    `plan the guard's pass` as an unterminated quote. strip_data already keeps
    such a body away from the raw-text heuristics; the tokenizer needs the same.
    Without it a plain `git commit -F - <<'EOF'` came back "command could not be
    parsed", and the cost was never only that prompt: a parse failure REPLACES
    the analysis, so an `rm -rf /` after the terminator reported as a vague ask
    instead of a deny. The suite hid this for as long as it existed, because
    every heredoc case in it arrives through a `"$(cat <<'EOF' ...)"` wrapper
    whose substitution masking already covered the body.

    Heredoc bodies are deliberately NOT in the returned map. Unmasking prose back
    into a token is the one thing this must not do, and classify_operand refuses
    any operand still carrying a sentinel.

    A SUBSTITUTION must survive shlex whole; see the module docstring for what
    happens when it does not. Heredocs are masked first, so
    `$(cat <<'EOF' ... fix(x): y ... EOF )` has its prose quotes and parentheses
    gone before _close_paren has to balance around them.

    Unquoted NEWLINES are normalised to ";" on the way past, for a related
    reason: shlex consumes them as whitespace, so a statement on the next line
    joined the one above it into a single segment. This is the only place in the
    file that can distinguish a separating newline from one inside a quoted
    string or one escaped as a line continuation, because it is the only place
    that tracks quote state over the raw text.

    Single quotes suppress substitution entirely; double quotes do not. An
    unterminated span is not masked, so it falls through to the ValueError arm
    in analyse() rather than being masked to something that parses cleanly and
    means nothing.
    """
    cmd = cmd.replace(SENTINEL, "").replace(MARK, "")
    parts, last = [], 0
    for hd, m in enumerate(HEREDOC_RE.finditer(cmd)):
        parts.append(cmd[last:m.start()])
        parts.append("%sHD%d%s" % (SENTINEL, hd, SENTINEL))
        last = m.end()
    parts.append(cmd[last:])
    cmd = "".join(parts)

    out, subs, quote, i, n = [], {}, None, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote == "'":
            if c == "'":
                quote = None
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            # A line continuation is DELETED by the shell -- `rm -rf \<nl>/` runs
            # as `rm -rf /`. shlex with posix=True disagrees: it unescapes the
            # pair into a literal newline still glued to what followed, handing
            # back the operand "\n/". posixpath.isabs() calls that relative, so
            # it classified as a bounded relative path and the guard returned
            # *allow* for a filesystem wipe. Deleting the pair here is both what
            # bash does and the only place with the quote state to know it
            # applies: single quotes are handled above, where a backslash is
            # literal and no continuation exists.
            if cmd[i + 1] == "\n":
                i += 2
                continue
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if quote is None and c in "'\"":
            quote = c
            out.append(c)
            i += 1
            continue
        if quote == '"' and c == '"':
            quote = None
            out.append(c)
            i += 1
            continue
        # An unquoted newline separates statements exactly as ";" does, and this
        # is the only place that can tell the two apart from a newline inside a
        # quoted string or one escaped as a line continuation -- the quote state
        # lives here. A `\` + newline never reaches this branch, because the
        # escape arm above consumed both characters and left the join intact.
        if quote is None and c == "\n":
            out.append(" ; ")
            i += 1
            continue
        # Unquoted or inside double quotes -- substitution is live in both.
        end = None
        if c == "$" and i + 1 < n and cmd[i + 1] == "(":
            end = _close_paren(cmd, i + 1)
        elif c == "`":
            end = _close_backtick(cmd, i)
        if end is not None:
            ph = "%sSUB%d%s" % (SENTINEL, len(subs), SENTINEL)
            subs[ph] = cmd[i:end + 1]
            out.append(ph)
            i = end + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), subs


def unmask(tok, subs):
    for ph, original in subs.items():
        tok = tok.replace(ph, original)
    return tok


def has_subst(s):
    """The same test classify_operand uses, so the two never disagree."""
    return "$(" in s or "`" in s


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
    """rclone: -> (destructive, targets, subcommand). sync/move destroy the
    destination; purge/delete/rmdir destroy what they name."""
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
        return False, [], ""
    sub = operands[0]
    if sub not in RCLONE_DESTRUCTIVE:
        return False, [], ""
    rest = operands[1:]
    if sub in ("sync", "move"):
        return True, rest[-1:], sub  # destination is pruned to match source
    return True, rest, sub           # purge/delete/rmdir destroy what they name


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
    """-> [Finding] for the non-rm commands in one segment."""
    out = []
    segment = argv
    idx, base = command_word(argv)
    argv = argv[idx:]
    if not argv:
        return out

    def rate(targets, label, missing, bare_var_ok=False):
        """Judge each target, tagging every Finding with the same why-this-one label."""
        if not targets:
            out.append(Finding("ask", segment, label, None, missing))
            return
        for t in targets:
            d, r = classify_operand(t, cwd, bare_var_ok=bare_var_ok)
            out.append(Finding(d, segment, label, t, r))

    if base == "find":
        destructive, roots = find_targets(argv)
        if destructive:
            rate(roots,
                 "find deletes everything under its search roots",
                 "no search root given, so find descends from the current directory")
    elif base in ("fd", "fdfind"):
        destructive, roots = fd_targets(argv)
        if destructive:
            # fd with no path argument searches the current directory, exactly
            # as `find . ` does -- so treat it as the literal `.` rather than as
            # a missing operand.
            rate(roots or ["."],
                 "fd -x/-X runs a destructive command on everything under its search roots",
                 "no search root given, so fd descends from the current directory")
    elif base == "rsync":
        destructive, dest, ok = rsync_targets(argv)
        label = "rsync --delete prunes the destination to match the source"
        if destructive and not ok:
            out.append(Finding("ask", segment, label, None,
                               "the destination operand could not be identified"))
        elif destructive:
            rate(dest, label, "no destination operand")
    elif base == "rclone":
        destructive, targets, sub = rclone_targets(argv)
        if destructive:
            label = ("rclone %s prunes the destination to match the source" % sub
                     if sub in ("sync", "move")
                     else "rclone %s destroys what it names" % sub)
            rate(targets, label, f"rclone {sub} with no operand")
    elif base in GIT_NAMES:
        destructive, paths = git_clean_targets(argv)
        if destructive:
            label = ("git clean removes untracked and ignored files, "
                     "which are not in history and cannot be restored")
            for t in paths:
                d, r = classify_operand(t, cwd, bare_var_ok=False)
                # Untracked and ignored files are not in history. Even a "safe"
                # path only earns a prompt, never a silent pass -- and saying
                # "literal relative path" there would read like a clean bill.
                if d == "allow":
                    d, r = "ask", ("the path itself is bounded, but nothing "
                                   "under it is recoverable")
                out.append(Finding(d, segment, label, t, r))
    elif base == "ssh":
        remote = ssh_remote_command(argv)
        if remote and depth < MAX_SSH_DEPTH:
            # No local cwd applies on the far end, so pass cwd="": only /tmp and
            # /var/tmp stay safe and every other absolute path asks. The findings
            # keep the REMOTE segment as their echo -- that is the command that
            # matters -- and carry the hop in their label.
            hop = "over ssh -- no local safe root clears a path on the far end"
            for f in analyse(remote, "", depth + 1):
                out.append(f._replace(
                    label=f"{hop}; {f.label}" if f.label else hop))
        elif remote:
            out.append(Finding("ask", segment, "over ssh", None,
                               "ssh nested too deeply to analyse"))
    return out


def analyse(cmd, cwd, depth=0):
    """-> [Finding]. Empty means the guard has no opinion."""
    # Raw scan first: no guarded command name anywhere means nothing to judge.
    if not GUARDED_CMD_RE.search(cmd):
        return []

    # Blank out `git rm`, then look again. If nothing guarded survives, the
    # command is git's business and none of ours. Done on the raw text so the
    # heuristics below never see a git rm either.
    text = GIT_RM_RE.sub(" git-rm ", cmd)
    # ...and blank the parts of the line that are data rather than code, so a
    # commit message quoting `git clean -fdx` is not read as running it.
    text = strip_data(text)
    if not GUARDED_CMD_RE.search(text):
        return []

    # The bail-outs below exist to stop a *destructive* command hiding inside
    # something unreadable. With no destructive pairing anywhere -- no recursion
    # flag next to an rm, no -delete next to a find -- there is nothing to hide,
    # so `ls | xargs rm` goes straight to the token scan instead of prompting.
    danger = danger_hint(text)

    # Mask substitutions first: punctuation_chars=True splits on ( and ), which
    # takes an unquoted $(...) apart into literals that each look harmless. The
    # sentinel keeps the span whole and keeps it attached to whatever follows it,
    # so classify_operand sees `$(pwd)/b` rather than `$(pwd)` and a stray `/b`.
    masked, subs = mask_opaque(cmd)

    try:
        lex = shlex.shlex(masked, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = [unmask(t, subs) for t in lex]
    except ValueError as e:
        # Same gate as every other bail-out: unreadable only matters if there is
        # something destructive to hide. shlex is stricter than the shell it
        # stands in for -- it rejects $'...' outright, and a stray quote in a
        # heredoc body that bash takes literally -- so ungated this fires on
        # commands that would have run perfectly well.
        if not danger:
            return []
        return [bare("ask", f"command could not be parsed ({e})")]

    bounds = segment_bounds(toks)

    def segment_at(i):
        """The pipeline segment token i belongs to, for echoing back."""
        for s, e in bounds:
            if s <= i < e:
                return toks[s:e]
        return list(toks)

    def bail(decision, i, reason, hi=None):
        """A bail-out that still echoes the command it choked on.

        Naming the construct is not enough by itself: `xargs` says which word
        stopped the guard and nothing at all about what xargs was told to do. So
        the segment goes back too, with everything from `hi` onward lit up --
        defaulting to the tokens AFTER the construct, because for something that
        hides what it runs, those arguments are the entire complaint.

        Falls back to a bare verdict if the token is somehow in no segment, which
        keeps a bail-out a bail-out rather than trading it for an IndexError.
        """
        for s, e in bounds:
            if s <= i < e:
                return [Finding(decision, toks[s:e], None, None, reason,
                                tuple(toks[(i + 1 if hi is None else hi):e]))]
        return [bare(decision, reason)]

    # Opaque constructs bail -- but only in *command position*. `.` is the POSIX
    # source builtin at the start of a segment and a jq filter in `jq -r . f`;
    # matching it positionally is the difference between the two.
    if danger:
        # A substitution in command position hides which command runs, so nothing
        # the operand checks below can prove says anything about it. Located via
        # command_word rather than "first token in the segment", so a substitution
        # behind a wrapper (`sudo $(x) -rf /`) is caught too.
        for start, end in bounds:
            j, _ = command_word(toks[start:end])
            if start + j < end and has_subst(toks[start + j]):
                return bail("ask", start + j,
                            f"{mark(toks[start + j])} runs in command position, "
                            "so which command runs is unknowable")
        for i, t in enumerate(toks):
            base = os.path.basename(t)
            in_cmd_pos = i == 0 or toks[i - 1] in SEPARATORS
            if in_cmd_pos and base in OPAQUE:
                return bail("ask", i, f"{mark(base)} hides its arguments -- what "
                                      "it runs on is not in the command")
            if base in SHELLS and i + 1 < len(toks) and toks[i + 1] == "-c":
                return bail("ask", i, f"{mark(base + ' -c')} hides its arguments "
                                      "-- the script is re-parsed, not run as written")
    if "--no-preserve-root" in toks:
        # The flag itself is the danger, so it is highlighted along with what
        # follows it rather than left to read as one more red flag among -rf.
        i = toks.index("--no-preserve-root")
        return bail("deny", i,
                    f"{mark('--no-preserve-root')} defeats rm's own safety net",
                    hi=i)

    in_git = git_segments(toks)
    findings = []

    # --- rm itself. Scanned across all tokens, not just command position, so
    # `sudo rm -rf` and `xargs rm -r` are both seen.
    found_rm = False
    i = 0
    while i < len(toks):
        if os.path.basename(toks[i]) not in RM_NAMES or in_git[i]:
            i += 1
            continue
        found_rm = True
        segment = segment_at(i)
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
        # A literal argument list settles whether this rm recurses. A substitution
        # in it does not: `rm $(echo -rf) /tmp/x` carries no -r the scanner can
        # see, and recursion is exactly what makes rm dangerous, so assume it.
        if not recursive and not any(has_subst(o) for o in operands):
            continue
        if not operands:
            continue
        for op in operands:
            d, r = classify_operand(op, cwd)
            # No label: for rm every operand is a target, which the echoed
            # command already makes obvious.
            findings.append(Finding(d, segment, None, op, r))

    # --- everything else, one segment at a time.
    for start, end in bounds:
        findings.extend(analyse_segment(toks[start:end], cwd, depth))

    if not found_rm and not findings:
        # Nothing survived parsing. Every construct that could execute a string
        # (eval, <shell> -c, xargs, substitution) already bailed above, so a bare
        # mention here is inert text -- unless it actually reads as a recursive
        # rm, in which case we failed to parse something real and must not
        # approve it. Searched against the git-stripped text, so an exempted
        # `git -C /path rm -r` does not read as unparsed.
        m = RM_RECURSIVE_RE.search(text)
        if m:
            frag = " ".join(m.group(0).split())  # the match may span a newline
            reason = (f"{mark(frag)} reads as a recursive rm, but no rm command "
                      "survived parsing")
            # Point at the token the rm is buried in. Reaching here means no
            # token's basename was `rm` -- a bare one would have been found --
            # so the match is inside a larger token, and that token is the whole
            # explanation: it shows `awk '{print "rm -rf /"}'` as a string awk
            # prints rather than as something anything is about to run.
            for i, t in enumerate(toks):
                if RM_RECURSIVE_RE.search(t):
                    return bail("ask", i, reason, hi=i)
            return [bare("ask", reason)]
        return []
    return findings


# --- rendering ---------------------------------------------------------------

def paint(style, s):
    # An empty style means "leave it alone" -- emitting a bare RESET there would
    # litter the echoed command with no-op escapes.
    return f"{style}{s}{RESET}" if style else s


def unknown_spans(tok):
    """-> sorted [(start, end)] of the spans in tok whose value is unknowable.

    A substitution always qualifies. An expansion qualifies only when it is NOT
    the whole token, and that restriction is the rule rather than an exception:

        $W/*     $W is unknowable, and /* is what turns "empty" into "/"
        $HOME    the guard knows exactly what this is -- that is why it denies
        $X       bare, so empty makes it an error, not a catastrophe

    Only the first has a half worth separating. The other two are flagged for
    what the guard DOES know about them, and dimming that to "cannot know" would
    misreport the finding sitting next to it.

    ${V:?}-guarded forms are skipped outright: they cannot be empty, they are
    what the reason tells you to write, and painting the fix like the fault
    undercuts the advice.
    """
    spans = subst_spans(tok)
    for m in EXPANSION_RE.finditer(tok):
        s, e = m.span()
        if e - s == len(tok) or GUARDED_RE.match(m.group(0)):
            continue
        if any(a <= s < b for a, b in spans):
            continue  # already inside a substitution: $(cat $F) is one opaque span
        spans.append((s, e))
    return sorted(spans)


def paint_token(style, tok):
    """Paint tok in `style`, except for its unknowable spans, which go UNKNOWN.

    Callers clip first, so a span cut in half by the truncation simply fails to
    parse and comes back in the base style -- never as a severed escape.
    """
    spans = unknown_spans(tok)
    if not spans:
        return paint(style, tok)
    out, prev = [], 0
    for s, e in spans:
        if s > prev:
            out.append(paint(style, tok[prev:s]))
        out.append(paint(UNKNOWN, tok[s:e]))
        prev = e
    if prev < len(tok):
        out.append(paint(style, tok[prev:]))
    return "".join(out)


def clip(s, n=MAX_TOKEN):
    return s if len(s) <= n else s[:n - 1] + "…"


MARK_RE = re.compile("%s([^%s]+)%s" % (MARK, MARK, MARK))
# The fix a reason hands you, always the ${VAR:?} form this guard exists to sell.
# Matched on the rendered text rather than marked at the source because it is
# built by one format string in classify_operand and never varies.
FIX_RE = re.compile(r'"\$\{[A-Za-z_][A-Za-z0-9_]*:\?\}"')


def paint_marks(reason):
    """Paint a reason's marked spans and its fix, and drop the markers.

    Marks are RED, because a marked span is a command name or a flag, which is
    what RED already means in echo_line. Yellow is for an operand about to be
    destroyed, and a bail-out has not identified one -- that is what makes it a
    bail-out.

    Painted through paint_token so a marked span renders exactly as it would
    inside an echoed command: `pwd` -rf / bails with the substitution named, and
    the substitution stays magenta there for the same reason it is magenta
    everywhere else -- it is the part whose value nothing here can know.
    """
    reason = MARK_RE.sub(lambda m: paint_token(RED, clip(m.group(1))), reason)
    return FIX_RE.sub(lambda m: paint(FIX, m.group(0)), reason)


def command_head(toks):
    """-> index one past the tokens that name the command: `rm`, `git clean`."""
    idx, base = command_word(toks)
    head = idx + 1
    subs = SUBCOMMANDS.get(base)
    if subs and head < len(toks) and toks[head] in subs:
        head += 1
    return head


def echo_line(toks, flagged):
    """The offending segment, rebuilt from the tokens the guard actually parsed.

    Deliberately not the raw text: the permission dialog already prints that
    verbatim above, so the useful thing to show here is the guard's own view of
    it -- `rm -rf "$W"/*` echoes as `rm -rf $W/*`, which is what got classified.
    """
    head = command_head(toks)

    def style(i):
        if i is None:                       # the elision marker
            return CONTEXT
        if toks[i] in flagged:              # what is about to be destroyed
            return YELLOW
        if i < head or toks[i].startswith("-"):
            return RED                      # what is doing the destroying
        return CONTEXT

    pieces = [(i, clip(t)) for i, t in enumerate(toks)]
    if sum(len(p) + 1 for _, p in pieces) > MAX_ECHO:
        # Too long to show whole. The command, its flags and every flagged
        # operand always survive; runs of anything else collapse to one ellipsis.
        kept, elided = [], False
        for i, p in pieces:
            if i < head or toks[i] in flagged or p.startswith("-"):
                kept.append((i, p))
                elided = False
            elif not elided:
                kept.append((None, "…"))
                elided = True
        pieces = kept
    return " ".join(paint_token(style(i), p) for i, p in pieces)


def blocks(findings):
    """Group findings into (segment, label, findings), first-seen order.

    Keyed on the label too, so the rare segment that trips two rules at once --
    `find /etc -exec rm -rf {} +` is both a find and an rm -- reports both
    rather than silently merging them under whichever label came first.

    Identical findings collapse: `git clean -fdxn | head; git clean -fdxn | wc -l`
    is two segments that tokenize the same and say the same thing, and printing
    that row twice reads as two separate problems.
    """
    order, groups, seen = [], {}, set()
    for f in findings:
        # segment is a list, so the Finding itself is unhashable -- key on a
        # tuple-ised copy rather than on f.
        ident = (f.decision, tuple(f.segment) if f.segment else None,
                 f.label, f.operand, f.reason, f.highlight)
        if ident in seen:
            continue
        seen.add(ident)
        key = ident[1], f.label
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    return [(k[0], k[1], groups[k]) for k in order]


def render(headline, findings):
    if len(findings) == 1 and findings[0].segment is None:
        # Nothing to point at, so the marked token carries all the emphasis.
        return f"{headline} -- {paint_marks(findings[0].reason)}"
    out = [headline]
    for segment, label, group in blocks(findings):
        out.append("")
        if segment:
            lit = {f.operand for f in group if f.operand}
            lit.update(t for f in group for t in f.highlight)
            out.append("  " + echo_line(list(segment), lit))
            if label:
                out.append("  " + paint(CONTEXT, label))
            out.append("")
        ops = [clip(f.operand) for f in group if f.operand]
        width = max((len(o) for o in ops), default=0)
        for f in group:
            if not f.operand:
                out.append("  " + paint_marks(f.reason))
                continue
            # Painted the same way again, so the row keys back to the operand in
            # the echo. Padding measures the unpainted text, which is what the
            # terminal actually renders once the escapes are consumed.
            op = clip(f.operand)
            out.append("  " + paint_token(YELLOW, op)
                       + " " * (width - len(op) + 2) + paint_marks(f.reason))
    return "\n".join(out)


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
        findings = analyse(cmd, cwd)
    except Exception as e:
        emit("ask", f"guard errored ({type(e).__name__}: {e}); asking to be safe")
    if not findings:
        passthrough()
    worst = max(RANK[f.decision] for f in findings)
    if worst == RANK["allow"]:
        # Nothing was blocked, so there is nothing to point at. Stay on one line.
        emit("allow", "destructive command on a provably safe target -- "
                      f"{paint_marks(findings[0].reason)}")
    # Only the operands that actually held the command up. A proven-safe operand
    # sitting next to a dangerous one is noise in a prompt about the dangerous one.
    blocked = [f for f in findings if f.decision != "allow"]
    if worst == RANK["deny"]:
        # The only headline that is painted. A deny and an ask are told apart by
        # their wording alone otherwise, and the wording is the part you read
        # last; leaving the ask plain is what makes this one carry.
        emit("deny", render(paint(RED, "refusing catastrophic delete"), blocked))
    emit("ask", render("destructive recursive command needs confirmation", blocked))


if __name__ == "__main__":
    main()
