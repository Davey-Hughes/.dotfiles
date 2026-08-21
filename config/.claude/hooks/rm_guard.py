#!/usr/bin/env python3
"""PreToolUse (Bash) guard for destructive recursive commands.

Scope is deliberately narrow: operations that *descend* and destroy. Anything
that cannot recurse is out of scope, because it cannot produce the failure this
guard exists to prevent -- `rm x` names one file no matter what x is.

Covered, each with its own notion of "which argument is the target":

    rm -r/-R                all operands
    find ... -delete        the search roots
    find ... -exec rm       the search roots
    fd -x/-X rm             the search roots, --search-path and -C included
    rsync --delete          the DESTINATION (last operand)
    rsync --remove-source-files   the SOURCE -- the opposite end
    rclone sync/purge/...   the destination / the named remote path
    git clean -f            the working tree
    git worktree remove -f  the worktree directory, untracked files included
    ssh host '<cmd>'        re-analysed remotely, with no local safe roots

An operand is judged over every path it can actually become, not as written:

    braces      `{/,/etc}` is TWO operands to the shell. Bash expands them
                first, before anything else, and the worst product wins.
    cd          a literal `cd` earlier on the line moves what `./x` means, so
                both the before and the after are judged.
    loop vars   `for f in /etc /var` binds $f in plain sight, which voids the
                "a bare $f is harmless when empty" rule -- it is never empty.

Deliberately NOT covered:

    non-recursive rm    without -r, rm cannot descend into a directory.
    git rm              a working-tree operation: only touches tracked files,
                        never untracked data, all of it recoverable from history.
                        `git clean` is the opposite and IS covered.
    git worktree remove without --force
                        git refuses it outright on a worktree holding an
                        untracked or modified file, and on a path that is not a
                        registered worktree at all -- so unlike rm -r it cannot
                        descend into a directory it was merely pointed at. Only
                        ignored files can go, and only from inside a worktree.
                        Verified against git 2.55; --force IS covered.
    rg                  read-only. `-x` is --line-regexp and `-r` is --replace
                        (rewrites printed output, never the file). A rule here
                        would only collide with fd's exec flags and misfire.
    dd, diskutil        catastrophic but device-shaped, not path-shaped. They
                        need a different check than a path classifier.

Within that scope it is an allowlist, not a blocklist: an operand must be
*proven* safe or the command falls through to `ask`. Every parse failure,
unknown construct, and unhandled case lands on `ask`, so a bug here costs a
prompt rather than a filesystem.

The one exception is RISKY_NEIGHBOURS, which is a blocklist, and the asymmetry
is what makes that sound. An `allow` from a PreToolUse hook bypasses permissions
for the WHOLE Bash call -- there is no per-segment scoping -- so proving one
delete safe was also approving whatever shared the line with it. That list
governs only how far an approval the guard already earned may spread, and a hit
WITHDRAWS the verdict rather than escalating it. A miss therefore costs exactly
the status quo: the guard cannot make a line more dangerous than staying silent.

Two gaps used to sit here, and they were the same question -- ${VAR:?} proves a
variable is NON-EMPTY, not that its target is safe. Both closed when the binding
map was allowed to clear as well as condemn: a leading literal binding now
resolves $W, ${W} and ${W:?} alike, and the resolved path is judged against
safe_roots like any other, so `W=/etc; rm -rf "$W"` and `W=/etc; rm -rf
"${W:?}"/*` both ask -- the command says /etc, and /etc is what gets judged. An
UNBOUND ${W:?} still clears with no path check, deliberately: the guarded form
is the assertion of intent this guard's own advice says to write, there is
nothing in the command to check it against, and distrusting it would cost a
prompt on exactly the command the author hardened.

Known gaps, left open on purpose:

    a stale binding clearing   no known vehicle -- which is not "closed", and
                               the distinction earned itself within a day. A
                               binding may clear an operand only after four
                               refusals miss: every later mention of the name is
                               a plain read (_read_only_mentions), nothing after
                               the bindings reaches eval/source/exec
                               (_indirection_present), and neither an assigning
                               parameter expansion nor anything between the
                               binding and the delete can rebind a name it never
                               writes (_rebinding_possible).

                               The fourth of those was added in review, not
                               design. The gate shipped with three and was
                               defeated by `PTR=W; : ${(P)PTR::=/etc}`, which
                               cleared as /tmp/safe/* and ran as `rm -rf /etc/*`
                               -- and, emptied, as `rm -rf /*`. Every fence
                               missed for exactly the reason its own docstring
                               gives, which is the point: these are four
                               independent partial rules, not a proof. Assume a
                               fifth vehicle exists and has not been found yet.

    cd "$D"; rm -rf ./build    ask, not allow. candidate_cwds refuses a cd whose
                               target it cannot read, so a relative operand
                               after one is unplaceable. proven_bindings could
                               often resolve $D -- and still is not asked to.
                               The map clears OPERANDS behind fences measured
                               against this machine's corpus; letting it place
                               the SHELL is a second trust the measurement says
                               nothing about, and a stale binding there puts the
                               rm in the wrong directory and clears `build`
                               anyway.

    a shell function           a function that reassigns in its body names
                               nothing at the call site. Narrowed, not closed:
                               a definition in the command text now drops
                               clearing (_rebinding_possible), zsh's autoload --
                               a body pulled off fpath, from outside the text --
                               trips the builtin rule there, and `zsh -c
                               'typeset +f'` on this machine returns nothing, so
                               no ambient function is left to call. That last
                               fact is a measurement of today's ~/.zshenv, not a
                               property of the design, and the narrowing rests
                               on it.

                               The autoload half rests on something narrower
                               still: the builtin rule matches in COMMAND
                               POSITION, so it holds only as far as command_word
                               is right about where that is. Three review passes
                               found three sets of prefixes it was wrong about
                               -- zsh's precommand modifiers, then five leading
                               redirect operators `REDIRECTS` never listed, then
                               bash's `{fd}>` -- each hiding a `typeset` and
                               returning allow on a command that runs as
                               `rm -rf //*`.

                               Twice the fix was a longer enumeration, and twice
                               the next pass beat it. What stands now is a shape
                               (REDIRECT_TOKEN_RE) and a backstop: a command
                               word that cannot BE one means the parse failed,
                               and clearing does not rest on a failed parse. The
                               lesson is kept rather than the reassurance --
                               this rule is exactly as good as command_word, and
                               command_word is a heuristic.

A third approach was built and withdrawn: prepending a runtime validator to the
command through the hook's `updatedInput`, so that approving a prompt would be
safe against an empty variable. Three designs, four review rounds, a Critical in
every one -- the last being a validator attached to the OUTER value of a name
bound by `for`/`read`/`local`, which made correct approved commands unrunnable
while the prompt claimed they were protected. A prepended validator cannot see
what the command will do: not the cwd after a `cd`, not a loop variable's value,
not whether $HOME is safe (which depends on cwd, since safe_roots folds it in).
See docs/superpowers/specs/2026-07-30-rm-guard-rewrite-and-deny-design.md.

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
what xargs was told to do, so what it hides is echoed alongside it.

The verdict is plain text. Claude Code renders permissionDecisionReason through
a component that strips control characters, so nothing here may rely on colour;
see the note above MAX_ECHO for what that cost and why it is not coming back.
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
    out = {"hookEventName": "PreToolUse",
           "permissionDecision": decision,
           "permissionDecisionReason": reason}
    print(json.dumps({"hookSpecificOutput": out}))
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
Finding = namedtuple("Finding",
                     "decision segment label operand reason highlight",
                     defaults=((),))


def bare(decision, reason):
    """A verdict with no operand to point at -- a parse failure or a bail-out."""
    return Finding(decision, None, None, None, reason)


# Every other verdict points at a token by echoing the segment and giving that
# token its own row. A bail-out has no operand to point at -- it is one
# sentence -- so it marks the span that explains it instead. The mark used to
# select what got painted; with the colour gone its remaining job is to bound
# the span for clipping, and two of the five call sites mark text of unbounded
# length: a token found in command position, and whatever fragment read as a
# recursive rm. See unmark.
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
# The cwd after a `cd` whose target could not be read. Deliberately NOT the
# empty string: "" already means "no cwd given" -- the far side of an ssh, where
# a named subdirectory is still bounded. This is not bounded. `cd "$D"` may have
# landed at `/`, which makes a later `build` into `/build`.
UNKNOWN_CWD = ""

RM_NAMES = {"rm"}
# A bare shell variable name, used to spot the target of `for NAME in ...`.
BARE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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
# ...and the same thing recognised by SHAPE rather than by membership, for
# deciding whether a token that leads a segment is a redirection to step over.
#
# The set above was used for that first, and enumerating was the bug: zsh's
# `<<<`, `>|`, `<>`, `&>` and `&>>` are all leading redirections it does not
# list, so command_word stopped on the operator and reported IT as the command.
# `<<<x typeset -g "$(printf '\127')=/"; rm -rf "$W"/*` came back allow and runs
# as `rm -rf //*`. That was the second enumeration in this spot to be defeated
# in one review; hence a shape, and hence the backstop in _rebinding_possible
# for the next operator neither of them anticipates.
#
# A redirection operator is optional fd digits, then `<` or `>`, then any run of
# the metacharacters that can decorate one -- or the `&>`/`&>>` forms, which
# lead with the ampersand instead. `|&` is deliberately unmatched: it is a
# pipeline operator, not a redirection, and it separates segments rather than
# prefixing one.
REDIRECT_TOKEN_RE = re.compile(r"^(?:\d*[<>][<>&|]*|&>>?)$")
# bash's `{fd}>file`, which names a variable to receive the descriptor. shlex
# emits `{fd}` as its own token, and it carries word characters, so it slipped
# past both the shape rule AND the backstop -- the backstop only refuses a
# command word that cannot be a NAME, and `{fd}` looks like one.
#
# zsh rejects the whole construct with a parse error, so this is not reachable
# from the Bash tool as it stands. It is fixed regardless: which shell runs the
# command is a measured fact about today's environment, not a property of this
# guard, and the same docstring that records the measurement says the narrowing
# rests on it.
FD_BRACE_RE = re.compile(r"^\{\w+\}$")
# What may precede an unquoted `#` for it to start a comment. Bash requires the
# start of a word, so `./build#x` is a filename and not a comment -- a
# distinction shlex does not make, which is the other half of the same bug.
COMMENT_BOUNDARY = " \t\n;&|("
# Constructs we cannot statically reason about.
OPAQUE = {"eval", "exec", "source", "xargs", "."}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
GIT_NAMES = {"git"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                  "--exec-path", "--config-env"}
# Words that prefix a command without changing which command it is.
#
# The first six spawn a subprocess, so they cannot rebind the calling shell and
# were only ever about naming the right command. The last three are zsh
# PRECOMMAND MODIFIERS -- they run the command IN the current shell, which makes
# their absence a wrong `allow` rather than a cosmetic miss:
#
#     W=/tmp/build; builtin typeset -g "$(printf '\127')=/"; rm -rf "$W"/*
#
# came back allow and runs as `rm -rf //*`. `builtin` was, word for word, the
# wrapper _rebinding_possible's docstring claimed could not hide a builtin from
# it. noglob and nocorrect do the same job.
WRAPPERS = {"sudo", "command", "env", "nohup", "time", "nice",
            "builtin", "noglob", "nocorrect"}
# ...and the keywords and grouping that can stand in front of one.
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!",
            "(", "{", ")", "}"}

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
# ...and the two that name where fd descends FROM. They were in the set above,
# so their value was consumed as a throwaway and `roots` fell back to ".":
# `fd --search-path / -x rm` came back *allow* for a walk of the whole
# filesystem. -C is the short form of --base-directory.
FD_ROOT_OPTS = {"--search-path", "--base-directory", "-C"}
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

# --- how far an `allow` is allowed to reach ------------------------------------
# A PreToolUse `allow` bypasses the permission system for the WHOLE Bash call --
# not for the operand this guard proved safe. There is no per-segment scoping.
# So proving one delete safe was silently approving everything sharing the line:
#
#     curl http://x/s | bash && rm -rf /tmp/build/out      -> allow
#     sudo dd if=/dev/zero of=/dev/sda; rm -rf /tmp/build/x -> allow
#
# Neither `curl | bash` nor `dd` is in this file's scope, and neither was
# examined. They were approved anyway, as a side effect.
#
# This is the one blocklist in an otherwise allowlist file, and the asymmetry is
# what makes that sound. The allowlist governs the DELETION TARGET, where a miss
# costs a filesystem. This governs only how far an approval the guard already
# earned is permitted to spread, so a miss here costs exactly the status quo --
# the guard cannot make a line more dangerous than not running at all. A hit
# withdraws the verdict rather than escalating it: normal permissions then decide,
# which is what would have happened had the guard never spoken.
#
# Measured against 233 real destructive commands from this machine's transcripts:
# 116 of them clear, and this withdraws none of them.
RISKY_NEIGHBOURS = (
    (r"(?<![\w-])(?:sudo|doas|pkexec|su)(?![\w-])", "privilege escalation"),
    (r"(?<![\w-])(?:dd|mkfs(?:\.\w+)?|fdisk|sfdisk|parted|wipefs|blkdiscard"
     r"|shred|hdparm|cryptsetup)(?![\w-])", "a raw device or disk write"),
    (r"(?:curl|wget)[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|k|da|fi)?sh(?![\w-])",
     "a download piped into a shell"),
    (r"(?<![\w-])(?:chmod|chown|chgrp|setfacl)(?![\w-])"
     r"(?=[^;&|\n]*(?:-[A-Za-z]*R|--recursive))(?=[^;&|\n]*\s/)",
     "a recursive permission change on an absolute path"),
    (r">\s*/dev/(?!null|stdout|stderr|tty|fd/)", "a write to a device node"),
    # `init` is deliberately absent: `git init`, `npm init` and `config init`
    # are ordinary, and matching the bare word withdrew three real allows for
    # nothing. Nobody reaches SysV init by typing it as an argument anyway.
    (r"(?<![\w-])(?:systemctl|shutdown|reboot|halt|poweroff|kexec)(?![\w-])",
     "service or power control"),
    (r"(?<![\w-])(?:iptables|ip6tables|nft|ufw|firewall-cmd)(?![\w-])",
     "a firewall change"),
    (r"authorized_keys|/etc/(?:sudoers|shadow|passwd|group)", "a credential file"),
    (r"(?<![\w-])(?:userdel|groupdel|usermod|useradd|passwd|chpasswd)(?![\w-])",
     "an account change"),
    # `at` is not here for the same reason `init` is not: two letters match far
    # too much prose and flag text to be worth the one scheduler nobody uses.
    (r"(?<![\w-])crontab(?![\w-])", "a scheduled job change"),
    (r"(?<![\w-])(?:kill|pkill|killall)\s+-9?\s*1(?![\d\w])", "killing init"),
)
RISKY_NEIGHBOURS = tuple((re.compile(r), why) for r, why in RISKY_NEIGHBOURS)


def risky_neighbour(text):
    """-> why this command line must not receive a blanket allow, or None."""
    for rx, why in RISKY_NEIGHBOURS:
        if rx.search(text):
            return why
    return None

# This verdict used to be painted -- the command red, the operands yellow, the
# spans it could not resolve magenta, the suggested ${VAR:?} cyan. Claude Code
# 2.1.235 stopped rendering SGR from a hook and started sanitising it: every
# externally-supplied string in the permission dialog now goes through a filter
# that maps each code point below 32 (bar tab and newline) to U+FFFD, so ESC
# came out as a replacement glyph and the prompt read `<?>[1;31mrm<?>[0m`.
#
# It is deliberate hardening against ANSI injection from a hook, so the colour
# is not coming back and nothing here should try to smuggle it through. The
# structure carries the emphasis instead: the echoed segment on its own line,
# then one row per flagged operand, keyed by repeating the operand at the front
# of its row. Anything added to re-mark the roles in punctuation would land in
# the echo, which is worth more read as the command it is.
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


# --- brace expansion ---------------------------------------------------------
# Bash expands braces FIRST -- before tilde, parameter and command substitution --
# and each product becomes a separate word. Not expanding them was the worst
# wrong-`allow` in this file's history, and it did not even need a variable:
#
#     rm -rf {/,/etc}       ->  rm -rf / /etc
#
# With no leading slash the whole token read as one bounded *relative* path, so
# `allow` came back for a command that deletes the filesystem root. The deny on a
# literal `/` was not defeated, it was routed around. `rm -rf /tmp/{../etc,x}`
# is the same shape one level in, and cost `/etc`.
#
# A group expands only when it holds an unquoted top-level `,` or a numeric
# range. `{}` is find's placeholder and `{a}` is a literal filename; both must
# survive untouched, which is why a single alternative is not an expansion.
#
# Quoting is invisible here -- shlex removed the quotes long ago, so a literal
# directory named `{a,b}` expands too. That direction is safe: every product is
# classified and the worst wins, so a spurious expansion can only tighten a
# verdict, never loosen one.
MAX_BRACE_PRODUCTS = 64
NUM_RANGE_RE = re.compile(r"^(-?\d+)\.\.(-?\d+)$")


def _split_alternatives(body):
    """Top-level `,` split of a brace body, ignoring nested groups."""
    parts, depth, cur, i, n = [], 0, [], 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            cur.append(body[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def _numeric_range(body):
    """-> ['1','2','3'] for `1..3`, or None. Non-numeric ranges stay literal:
    leaving them alone can only produce a path with a `{a..z}` component in it,
    which classify_path judges on its own and never mistakes for something
    shallower than it is."""
    m = NUM_RANGE_RE.match(body)
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2))
    step = 1 if hi >= lo else -1
    if abs(hi - lo) + 1 > MAX_BRACE_PRODUCTS:
        return None
    return [str(v) for v in range(lo, hi + step, step)]


def _brace_group(s):
    """-> (start, end, alternatives) for the leftmost expandable group, or None."""
    i, n = 0, len(s)
    while i < n:
        if s[i] == "\\":
            i += 2
            continue
        if s[i] != "{":
            i += 1
            continue
        depth, j = 0, i
        while j < n:
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j < n:
            body = s[i + 1:j]
            alts = _split_alternatives(body)
            if len(alts) > 1:
                return i, j, alts
            rng = _numeric_range(body)
            if rng:
                return i, j, rng
        i += 1
    return None


def expand_braces(word, limit=MAX_BRACE_PRODUCTS):
    """-> every word bash would produce. [word] when there is no expansion.

    Truncated at `limit` products rather than allowed to blow up: a nested
    expansion is multiplicative, and classify_operand treats a truncated list as
    unprovable instead of judging a prefix of it.
    """
    g = _brace_group(word)
    if not g:
        return [word]
    i, j, alts = g
    pre, post = word[:i], word[j + 1:]
    out = []
    for a in alts:
        for p in expand_braces(pre + a + post, limit):
            out.append(p)
            if len(out) >= limit:
                return out
    return out


def classify_path(p, cwd):
    """Classify a fully-literal path. -> (decision, reason)"""
    if not posixpath.isabs(p):
        # A literal relative path cannot become "/" at any cwd. Bounded blast
        # radius, so it does not need cd-tracking to clear.
        if ".." in p.split("/"):
            return "ask", "relative path escapes upward via '..'"
        if cwd == UNKNOWN_CWD:
            return "ask", ("an earlier `cd` in this command goes somewhere the "
                           "guard cannot resolve, so where this lands is unknown")
        # A named subdirectory is bounded wherever cwd happens to be. An operand
        # that IS cwd -- `.`, `./`, or the `.` a `./*` glob strips down to --
        # takes the whole tree you are standing in, which is a different thing
        # to agree to and gets its own prompt.
        whole_cwd = posixpath.normpath(p) == "."
        # `.` IS cwd, and `./x` is one level under it. Harmless in a project
        # directory, catastrophic at / or $HOME. cwd is in the hook payload, so
        # resolve and check those cases.
        if cwd and posixpath.isabs(cwd):
            resolved = posixpath.normpath(posixpath.join(cwd, p))
            home = os.environ.get("HOME", "")
            depth = len([x for x in resolved.split("/") if x])
            if resolved == "/" or depth <= 1:
                return "deny", f"relative path resolves to {resolved}"
            if home and resolved == posixpath.normpath(home):
                return "deny", "relative path resolves to the home directory"
            if whole_cwd:
                return "ask", ("operand is the working directory itself "
                               f"({resolved}), so everything under it goes")
            return "allow", "literal relative path"
        # No cwd to resolve against -- this is the far side of an ssh, where the
        # working directory is whatever the remote login lands in, usually $HOME.
        if whole_cwd:
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


def worst_of(results):
    """-> the harshest (decision, reason) in results, ties going to the first."""
    best = None
    for d, r in results:
        if best is None or RANK[d] > RANK[best[0]]:
            best = (d, r)
    return best or ("ask", "nothing to classify")


def classify_operand(op, cwd, bare_var_ok=True, assigned=None, loops=None,
                     may_clear=False):
    """-> (decision, reason), over every path this operand can actually become.

    Two things multiply a single written operand into several real ones, and
    both are resolved here rather than inside classify_one so that function
    stays a judgement about ONE path:

        braces    `{/,/etc}` is two operands to the shell, not one token
        cwd       a literal `cd` earlier on the line moves what `./x` means,
                  and a conditional one means it may or may not have moved

    Every product is judged and the worst wins, which is the only reading that
    matches what the command does: rm is handed all of them.

    may_clear is carried through untouched; it is a fact about where this
    operand's COMMAND sits in the line, which neither brace expansion nor a
    candidate cwd can change.
    """
    cwds = [cwd] if isinstance(cwd, str) else list(cwd) or [""]
    products = expand_braces(op)
    if len(products) >= MAX_BRACE_PRODUCTS:
        return "ask", "brace expansion produces too many paths to check"
    results = []
    for p in products:
        for c in cwds:
            d, r = classify_one(p, c, bare_var_ok, assigned, loops, may_clear)
            if p != op:
                r = f"brace expansion produces {p}; {r}"
            results.append((d, r))
    return worst_of(results)


def classify_one(op, cwd, bare_var_ok=True, assigned=None, loops=None,
                 may_clear=False):
    """-> (decision, reason). Anything not provably safe returns ask/deny.

    bare_var_ok encodes an rm-specific fact: `rm -rf $X` with X unset or empty
    is an *error*, not a catastrophe, so a bare expansion is safe there. It is
    not safe for commands that treat "no path" as "use the current directory" --
    `find $X -delete` with X unset becomes `find -delete`, which is `rm -rf .`.

    may_clear is analyse's finding that nothing before this operand's command
    could have rebound a proven binding -- see _rebinding_possible. False is
    the safe default: a caller that does not know gets condemn-only.
    """
    if "`" in op or "$(" in op:
        return "ask", "command substitution cannot be statically resolved"

    # A binding condemns from anywhere and clears only behind two gates. The
    # map is built by static inference over shell text, and six review rounds
    # on the condemn-only design found six ways to make it claim a binding the
    # shell had already overwritten -- each of which would have been a wrong
    # `allow` on `rm -rf /*`. Condemning needs no gate against that: a stale
    # binding there costs a missed deny, which is a prompt. Clearing is what a
    # stale binding turns into a wiped filesystem, so it additionally requires:
    #
    #   may_clear   nothing between the binding run and this command can rebind
    #               a name without writing it. The rebindings that DO write it
    #               are already gone -- proven_bindings drops any name whose
    #               later mentions are more than plain reads.
    #   non-empty   W= proves set-but-empty, the catastrophe case itself.
    #               Substituting "" is still what produces the deny on
    #               `W=; rm -rf "$W"/*` -> /*, but it must not clear anything:
    #               `W=; rm -rf "$W"` runs as a harmless `rm -rf ""`, and ""
    #               resolved as a path reads as the working directory, which
    #               would mis-deny an error-out.
    if assigned:
        def bound(m):
            val = assigned.get(m.group(1) or m.group(2))
            # ${W:?} never expands an empty value -- it aborts, and the delete
            # does not run -- so substituting "" there would judge a path the
            # shell never produces. $W and ${W} do expand to "", and that ""
            # is what turns "$W"/* into /*.
            if val is None or (val == "" and ":?" in m.group(0)):
                return m.group(0)
            return val
        resolved = SUBST_NAME_RE.sub(bound, op)
        if resolved != op and not EXPANSION_RE.search(resolved):
            d, r = classify_one(resolved, cwd, bare_var_ok)
            used = [n for n in dict.fromkeys(
                m.group(1) or m.group(2) for m in SUBST_NAME_RE.finditer(op))
                if n in assigned]
            if d == "deny" or (may_clear and all(assigned[n] for n in used)):
                shown = ", ".join("%s=%s" % (n, assigned[n]) for n in used)
                return d, f"{shown} in this command, so this reads as {resolved}; {r}"

    # A loop BINDS the name, and unlike the assignment map -- which can be stale,
    # which is why clearing from it is gated -- a loop's word list is complete,
    # visible, and every word in it runs. So this one clears and condemns ungated.
    #
    # It has to. The bare-expansion allow is unsound for a loop variable:
    #
    #     for f in /etc /var; do rm -rf "$f"; done
    #
    # cleared as "bare $f with no suffix", on the grounds that an empty $f makes
    # `rm -rf ""` -- an error rather than a catastrophe. $f is never empty here.
    # It is /etc. A `while read f` loop is the other half: the value comes from
    # stdin, so it is not knowable at all and the same free pass is just as wrong.
    if loops:
        names = [n for n in dict.fromkeys(
            m.group(1) or m.group(2) for m in SUBST_NAME_RE.finditer(op))
            if n in loops]
        if len(names) > 1:
            return "ask", ("several loop variables in one operand, which this "
                           "resolves one at a time")
        if names:
            name, values = names[0], loops[names[0]]
            if values is None:
                return "ask", (
                    f"${name} is bound by a loop to a value that is not in this "
                    "command, so an empty one is not the only way it goes wrong")
            out = []
            for val in values:
                sub = SUBST_NAME_RE.sub(
                    lambda m, v=val: v if (m.group(1) or m.group(2)) == name
                    else m.group(0), op)
                # may_clear rides along: `for m in i12 i14; do rm -rf $SC/$m`
                # resolves $m here and leaves $SC for the binding block one hop
                # down, which is the only path by which an operand naming both
                # a loop variable and a bound one can clear.
                d, r = classify_one(sub, cwd, bare_var_ok, assigned,
                                    may_clear=may_clear)
                out.append((d, f"the loop sets ${name} to {val}; {r}"))
            return worst_of(out)

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

    Used to mask a substitution's contents before the command is tokenized.
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
        # An unquoted `#` at a word boundary comments out the rest of the line.
        # It has to be removed HERE, while the newline that bounds it is still a
        # newline: the arm below turns newlines into ";", and shlex's own
        # commenters then had no line end to stop at, so a comment swallowed
        # every statement after it -- `rm -rf ./build # x` and an `rm -rf /` on
        # the next line came back *allow*, with the second rm never tokenized.
        # Bash starts a comment only at the start of a word, which is why the
        # boundary test is here and not left to shlex.
        if quote is None and c == "#" and (not out or out[-1][-1:] in COMMENT_BOUNDARY):
            nl = cmd.find("\n", i)
            i = n if nl < 0 else nl
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


def tokenize(cmd):
    """-> tokens, with opaque spans masked and unquoted newlines normalised.

    Raises ValueError on an unbalanced quote, which analyse() turns into a
    bail-out. A named function rather than inline code in analyse() because
    the mask-then-lex-then-unmask sequence is exactly the shape mask_opaque's
    own docstring assumes a reader already understands, and spelling it out
    here once keeps analyse() itself reading as segmentation and classification
    rather than shlex plumbing. proven_bindings does not call this -- it reads
    the masked source directly, never the token stream -- but
    _indirection_present does, as the second of the two readings it unions.
    """
    masked, subs = mask_opaque(cmd)
    lex = shlex.shlex(masked, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""   # mask_opaque already removed real comments; leaving
                          # this at its default `#` would also cut `./build#x`
                          # mid-word, which bash treats as a filename.
    return [unmask(t, subs) for t in lex]


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
    """-> (index, basename) of the real command, past wrappers and VAR=val.

    Shell keywords and grouping are stepped over with the wrappers: a segment
    inside a loop body starts `do rm -rf ...`, and reading `do` as the command
    both named `do` as the thing destroying and hid a `do cd /` from
    candidate_cwds.

    A LEADING redirection is stepped over too. `>out rm -rf /` is an rm with a
    redirect in front of it, and bash and zsh both accept the form. shlex splits
    `2>/dev/null` into three tokens, so the fd digit has to be consumed with the
    operator -- without that, command_word stopped on the token `2` and
    `2>/dev/null typeset -g ...` hid the typeset from _rebinding_possible.
    """
    j = 0
    while j < len(argv):
        t = argv[j]
        if ENV_ASSIGN_RE.match(t) or t in KEYWORDS or os.path.basename(t) in WRAPPERS:
            j += 1
            continue
        # `2` `>` `/dev/null`, `>` `/dev/null`, a fused `2>` `/dev/null`, or
        # bash's `{fd}` `>` `/dev/null`.
        k = j + 1 if ((t.isdigit() or FD_BRACE_RE.match(t)) and j + 1 < len(argv)
                      and REDIRECT_TOKEN_RE.match(argv[j + 1])) else j
        if REDIRECT_TOKEN_RE.match(argv[k]) and k + 1 < len(argv):
            j = k + 2
            continue
        break
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


# --- bindings the command proves about itself --------------------------------
# `rm -rf "$W"/*` is unprovable on its own and rightly asks. It stops being
# unprovable when the same command assigns W a literal first:
#
#     W=/tmp/build; rm -rf "$W"/*
#
# See proven_bindings for what restricting this to a leading run buys. The one
# precondition worth flagging up here, because it is easy to miss reading
# proven_bindings alone, is that it depends on mask_opaque having already
# normalised unquoted newlines to ";". Before that runs, these two are the same
# text to anything that only looks at whitespace:
#
#     W=/tmp/build\nrm -rf "$W"/*    sequential -- the binding holds
#     W=/tmp/build rm -rf "$W"/*     env prefix -- it does NOT, because the shell
#                                    expands $W from the OUTER scope before the
#                                    assignment takes effect
#
# and it is the ";" that mask_opaque puts in the first one's place that makes
# BINDING_RE stop the run after `W=/tmp/build` instead of swallowing `rm` as
# part of a second, bogus assignment attempt.
# The three expansion forms a literal binding fully determines: $W, ${W} and
# ${W:?...} all evaluate to exactly W's value when W is set and non-empty, which
# is what such a binding proves. ${W:-default} is deliberately NOT matched -- it
# evaluates to the DEFAULT when W is empty, so the binding does not decide it.
#
# The `:?` message is matched as [^}]*, which is not bash's nesting-aware parse:
# `${W:?${X}}` matches only through the INNER `}`, leaving a stray `}` on the
# resolved path. That divergence runs in the strict direction and can only run
# that way -- a spurious extra character lengthens a path component, which can
# fail a safe-root prefix test but never pass one that the true path would fail.
# Left as it is rather than grown a brace counter for a case nobody writes.
SUBST_NAME_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::\?[^}]*)?\}|\$([A-Za-z_][A-Za-z0-9_]*)")
# NAME=VALUE where VALUE needs no interpretation. The charset excludes every
# character that would require a quote, an escape or an expansion to survive,
# which is exactly the set on which a second reading of the source could have
# disagreed with shlex's. That is the point: the previous design cross-checked
# the token stream against a hand-written scanner, and all three holes it sprang
# were divergences between the two readings.
#
# `~` is excluded for the same reason despite needing none of those: bash
# tilde-expands the right-hand side of an assignment at its start and after
# every unquoted `:`, so `W=~` and `W=/a:~/b` are not the literals they look
# like -- they are $HOME and "/a:$HOME/b". A charset that let `~` through would
# vouch for a value the shell does not actually produce.
BINDING_RE = re.compile(
    r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)=([^\s'\"\\;&|()<>$`~" + SENTINEL + r"]*)"
    r"[ \t]*(;|&&|\Z)")


def _read_only_mentions(name, rest):
    """Is every mention of `name` in the rest of the command a plain read?

    Anything that is not `$name`, `${name}` or `${name:?...}` drops the binding.
    The `:?` form is a read with an abort bolted on -- it can never write -- and
    it is the form this guard's own advice tells the author to use, so rejecting
    it would drop the binding on exactly the commands that took the advice. The
    other `${name:X}` modifiers stay out: `${name:=x}` is the one that writes,
    and telling the safe ones apart is the catalogue this rule exists to avoid.
    One blunt rule stands in for that catalogue of every way a shell can rebind
    a variable BY NAMING IT -- it does not, and cannot, cover a rebinding that
    never names its target at all:

        unset W    read W    W+=/x    export W=/x    ((W=1))    for W in ...
        declare W  local W   W=other  eval "W=/"     (W=/)      ${W:-/}

    It over-rejects -- `echo "$W"` keeps its binding but `echo W` loses it --
    which is the direction that costs a prompt rather than a filesystem.

    `eval "$C"`, `source ./f` and `. ./f` rebind without ever writing the
    variable's name in this command's text -- the name lives in $C or in the
    sourced file, both invisible here. That hole is not this function's to
    close: proven_bindings refuses the whole command when _indirection_present
    flags eval/source/./exec/xargs in EITHER a raw split or a shlex reading of
    what follows the bindings -- a union of two readings, not a reconciliation,
    so a disagreement between them can only refuse more. That closes the
    wrapper and quoting forms neither reading alone caught, but not every
    indirection: a shell FUNCTION that reassigns the variable in its body
    names nothing but the function at the call site, and there is no cheap
    check for what runs inside one. _rebinding_possible fences that off from
    CLEARING -- any definition in the command text drops it -- and condemning
    tolerates a stale binding by design.
    """
    for m in re.finditer(r"(?<!\w)%s(?!\w)" % re.escape(name), rest):
        s = m.start()
        if s >= 1 and rest[s - 1] == "$":
            continue
        if s >= 2 and rest[s - 2:s] == "${" and rest[m.end():m.end() + 1] == "}":
            continue
        if s >= 2 and rest[s - 2:s] == "${" and rest[m.end():m.end() + 2] == ":?":
            continue
        return False
    return True


def _indirection_present(rest):
    """Does the rest of the command reach eval / source / . / exec / xargs?

    Those rebind without ever naming the variable, so _read_only_mentions is
    blind to them by construction. analyse() bails on them too, but only in
    command position -- `command eval "$C"` hides the word from that check, and
    `builtin` is not even a recognised wrapper.

    Two readings, and either one is enough to refuse. That is deliberately a
    union rather than a reconciliation: this function used to cross-check two
    readings of the source POSITIONALLY, and every defect it shipped came from
    one reading vouching for the other when they disagreed. Here a disagreement
    can only reject more. The raw split catches `eval$IFS"$C"`, where shlex
    keeps the word glued together; the shlex pass catches `\\eval`, `"eval"` and
    `ev"a"l`, where the raw text never spells the word. An unreadable remainder
    refuses outright, which is also what `$'eval'` lands on.

    Over-rejects freely, and that costs a prompt: `git add .`, `find . -name y`
    and `cp -r . /dst` all end the run because `.` is in the set. `exec` and
    `xargs` cannot rebind the parent shell at all -- they are refused because
    the set is shared with analyse()'s bail-out and a narrower copy here would
    be one more thing to keep in step.
    """
    words = re.split(r"[\s;&|()<>$]+", rest)
    try:
        words += [w.lstrip("$") for w in tokenize(rest)]
    except ValueError:
        return True
    return any(os.path.basename(w) in OPAQUE for w in words)


def proven_bindings(cmd):
    """-> {name: literal} the command proves about its own variables.

    Only the LEADING run of assignments counts, which buys three properties a
    general dataflow pass would each have to earn: they are unconditional,
    because nothing has run before them; a heredoc body cannot supply one,
    because mask_opaque has already reduced it to a sentinel the value charset
    excludes; and `echo hi; W=/tmp/x; ...` proves nothing, because clearing it
    would mean proving `echo hi` cannot touch W.

    Read from the masked SOURCE rather than the token stream, and that is the
    whole design. shlex performs quote removal, but bash recognises an
    assignment BEFORE it -- so `'W=/x'` is a command name whose lookup fails,
    leaving W unset, while arriving as the same token a real `W=/x` produces.
    Reconstructing that from the source alongside the tokens needs two readings
    kept in lockstep, and every divergence between them was a wrong *allow* on
    `rm -rf /*`: an existence check let a genuine `W=/tmp/ok` vouch for a later
    quoted `'W=/tmp/evil'`, and a scanner blind to backslashes split
    `A=x\\;W=/tmp/ok` where shlex did not.

    So a value that needed quoting is a value this cannot vouch for, and it
    falls through to `ask`. That is the allowlist rule applied to the scanner
    itself, and it costs a prompt rather than a filesystem.

    Separator handling falls out of BINDING_RE: only `;`, `&&` and end of input
    continue the run. `|` and `&` are outside the value charset and end the
    match, which is right -- a pipeline segment's assignment never escapes its
    subshell, and `A=x || rm ...` runs the rm only if the assignment failed.
    """
    masked, subs = mask_opaque(cmd)
    bindings, pos = {}, 0
    while True:
        m = BINDING_RE.match(masked, pos)
        if not m:
            break
        bindings[m.group(1)] = m.group(2)   # last wins, exactly as the shell does
        pos = m.end()
        if not m.group(3):                  # matched \Z; nothing follows
            break
    # Unmasked, not the raw `masked[pos:]`: mask_opaque hides a command
    # substitution behind a sentinel so shlex cannot be shattered by it, but
    # that sentinel also hides a rebinding written INSIDE one -- `$((W=1))` is
    # arithmetic that assigns, and with the mask left in place the mention scan
    # below sees only an opaque placeholder and calls the binding untouched.
    # Substituting the original text back in is safe here in a way it is not
    # for tokenizing: this is a linear mention scan, not shlex, so a
    # substitution's own quotes and parens cannot shatter it. A shell function
    # that reassigns the variable in its body is the same class of hole -- a
    # rebind with no textual mention out here -- and stays a known limit of
    # this scan; _rebinding_possible is what stands between it and a cleared
    # operand.
    rest = unmask(masked[pos:], subs)

    # eval, source and `.` rebind without ever naming the variable, which is
    # the one hole _read_only_mentions cannot see. Deciding it here keeps the
    # map's safety inside the function that builds it; see _indirection_present
    # for why a single raw split over the remainder was not enough to decide it.
    if _indirection_present(rest):
        return {}

    return {n: v for n, v in bindings.items()
            if n not in DANGEROUS_VARS and _read_only_mentions(n, rest)}


# --- how long a proven binding stays provable ---------------------------------
# proven_bindings drops a name mentioned as anything but a plain read, and
# refuses whole commands that reach eval/source/exec. What survives both is a
# rebinding whose target name never appears in the text at all. Condemning does
# not care -- a stale binding there is a missed deny, a prompt -- but clearing
# from a stale binding is a wrong `allow` on `rm -rf /*`, so anything that COULD
# rebind namelessly has to stand between an operand and a cleared verdict.

# The builtins that can write a variable whose NAME arrives as data, so no
# mention scan can see the target: `declare -g "$(printf W)=/"` never spells W.
# trap and autoload are not padding. A trap's action can be computed the same
# way and fires at a point no textual order can place, and zsh's autoload pulls
# a function body off fpath -- a definition from OUTSIDE the command text, the
# one source the module docstring's typeset +f measurement cannot rule out.
REBINDING_BUILTINS = {"declare", "typeset", "local", "export", "readonly",
                      "printf", "read", "mapfile", "readarray", "getopts",
                      "let", "unset", "trap", "autoload"}
# A loop body re-runs, so a construct inside one is not bounded by where it sits
# in the text -- it happens again at the TOP of every later iteration. Tracked as
# a stack rather than "is there a loop anywhere", which was the first cut and was
# far too blunt: it read a `for` printing results AFTER a delete as a reason to
# distrust the delete, and withheld four real allows in the corpus for nothing.
#
# `repeat N; do ... done` and `foreach x (...) ... end` are zsh loops too, and
# both were missed outright. The closers matter as much as the openers, and the
# asymmetry between them decides which way a mistake fails: a stray opener
# pushes a phantom loop, which only withholds an allow, while a stray CLOSER
# pops a real one and re-permits the ordering this exists to void. So a closer
# counts only in command position -- `echo done` inside a body, and a `done`
# sitting in a `for` word list, were both popping a genuinely open loop.
#
# Matched by KIND. Restricting a pop to command position stopped a closer that
# was DATA (`echo done`, a `done` in a word list) from closing a loop; it did
# nothing about a closer that genuinely is a command word and closes nothing.
# `end` inside a `for ... do ... done` body is a command-not-found in bash, the
# loop keeps running, and popping on it re-permitted the ordering this exists to
# void. A mismatched closer now leaves the loop open, which is the conservative
# reading and costs at most a prompt.
LOOP_CLOSER_FOR = {"for": "done", "select": "done", "while": "done",
                   "until": "done", "repeat": "done", "foreach": "end"}
LOOP_OPENERS = set(LOOP_CLOSER_FOR)
LOOP_CLOSERS = set(LOOP_CLOSER_FOR.values())
# Commands whose name is punctuation. The backstop below refuses a command word
# it cannot identify, and "has no word character" was the first test for that --
# which called `[ -z "$f" ]` unidentifiable and cost two real allows in the
# corpus. `[` and `[[` are test, `:` is the no-op; none can assign to anything.
PUNCT_COMMANDS = {"[", "[[", ":"}
# set's -o (and zsh's +o) consumes the option name after it, so
# `set -euo pipefail` is all flags. Anything else that is not a -flag assigns:
# zsh's `set -A name ...` fills an array.
SET_OPT_ARG_RE = re.compile(r"^[-+][A-Za-z]*o$")
SET_FLAG_RE = re.compile(r"^[-+][A-Za-z]+$")
# The split _indirection_present applies to the raw text, applied here to each
# token: shlex keeps `printf$IFS-v` one word, and the running shell does not.
# Testing the token AND its split-out words is the same union of readings as
# there, and for the same reason -- a disagreement may only refuse more.
TOKEN_WORDS_RE = re.compile(r"[\s;&|()<>$]+")
# A parameter expansion that assigns through a name it never writes. zsh's
# ${(P)ptr::=value} sets the parameter NAMED BY ptr, and needs no command word
# at all -- it rode in as an argument to `:`:
#
#     W=/tmp/safe; PTR=W; : ${(P)PTR::=/etc}; rm -rf "$W"/*
#
# cleared as /tmp/safe/* and ran as `rm -rf /etc/*`. The empty form runs as
# `rm -rf /*`. All three fences missed at once, and each for its stated reason:
# W is never written, so _read_only_mentions sees only reads; nothing reaches
# eval or source, so _indirection_present is quiet; and the assignment is an
# ARGUMENT, so there is no command word for the builtin rule to match. This is
# the vehicle the docstring's "no known vehicle" was waiting for.
#
# Matched on RAW TEXT, because shlex shatters the construct exactly as it
# shatters an unquoted $(...) -- `${(P)PTR::=/etc}` arrives as
# ['${', '(', 'P', ')', 'PTR::=/etc}'] -- so no token-level rule can see it.
# That is also why a hit refuses from index 0 instead of from where it sits:
# once shlex has taken it apart there is no position to report, and the honest
# conservative answer is that the whole line loses clearing.
#
# Two shapes, both deliberately blunt. `${(` is any zsh expansion FLAG list,
# which is what makes (P) reachable in the first place; `::=` is the
# always-assign operator, the only one of ${n=v} / ${n:=v} / ${n::=v} that
# fires when the target is already set. Measured across 28,210 commands in this
# machine's transcripts: the only hits are the session that found this, and not
# one of them is a delete.
ASSIGNING_EXPANSION_RE = re.compile(r"\$\{\s*\(|::=")


def _rebinding_possible(toks, text=""):
    """-> (index, reason) of the first token that could rebind a name it never
    writes, or None if nothing here can.

    analyse reads only the index. The reason is carried for anyone bisecting a
    verdict by hand -- "which rule withheld this?" is the first question a
    surprising `ask` raises, and recomputing it from the tokens is worse than
    returning it. The suite does NOT read it; it asserts verdicts end to end,
    which is the right level and leaves this string unchecked. It is
    deliberately NOT put in the verdict text:
    withholding clearing does not decide the operand, it only declines to help,
    and the operand's own reason ("unguarded expansion with a suffix -- use
    ${W:?}") is the one that tells the reader what to do about it.

    The gate on CLEARING from proven_bindings, not on the map itself. Four
    shapes can rebind namelessly. The first is found on `text`, the raw command,
    because shlex takes it apart; see ASSIGNING_EXPANSION_RE. The other three
    are found on tokens:

        a function definition     `f() {` or `function f`: the one construct
                                  that defers execution past the reach of any
                                  textual order. The body may write anything,
                                  whenever it is called.
        an assigning builtin      REBINDING_BUILTINS above -- every one is
                                  already fatal to a binding when it names the
                                  variable; this catches the computed name.
        `set` with a non-flag     zsh's `set -A name ...`. set -u, set -e and
        argument                  set -o pipefail are all flags and still clear.

    The builtins are matched in COMMAND POSITION only, via command_word so a
    wrapper or a `do` does not hide one. A builtin rebinds nothing when it is
    somebody's argument, and reading an argument as code is the mistake this
    file keeps paying for -- here it was

        echo "### EXPERIMENT A: ... with a failing atrm (no trap backup)"

    where the word `trap` in an experiment's title withheld the allow on the
    delete two segments later. The function-definition test stays positionless:
    `function` and `()` are compared as whole tokens, so prose carrying either
    word arrives quoted, as one token, and cannot match.

    An assignment token is skipped outright: it writes a LITERAL name, which is
    _read_only_mentions' territory, and its value never executes -- without the
    skip, W=/usr/bin/printf would read as running printf.

    Position is why this returns an index and not a bool. Segments run left to
    right, so a construct after the rm cannot have run before it and costs
    nothing -- which is every hit in the measured corpus: printf and local as
    ordinary output, downstream of the delete.

    A loop body is the exception, because it runs again:

        for i in 1 2; do rm -rf "$W/x"; g; done

    `g` sits after the rm in the text and before it in time, on the second
    iteration. So a hit inside an open loop reports the OUTERMOST enclosing
    loop's index instead of its own -- the earliest point from which that body
    can have run. Only loops still open at the hit count: a `for` that prints
    results after the delete has already closed, or never contained it, and
    reporting 0 for it withheld four real allows in the corpus.
    """
    # Before anything positional: an assigning expansion cannot be placed in the
    # token stream at all, so it refuses the whole line.
    if ASSIGNING_EXPANSION_RE.search(text):
        return 0, ("a parameter expansion can assign through a name it "
                   "never writes")
    at = reason = None
    loop_starts = []
    # Where each segment's real command sits. command_word rather than "first
    # token after a separator", so `sudo export ...` and a loop body's
    # `do printf ...` both land on the word that runs.
    #
    # If command_word lands on something that cannot BE a command name, it did
    # not find the command -- it ran out of things it recognised and stopped on
    # a token it could not classify. Clearing then rests on a parse that already
    # failed, which is how `<<<` and `&>` each returned allow on a wipe. So the
    # whole line loses clearing instead.
    #
    # This is the rule that should have been written first. Two enumerations in
    # this spot were defeated in a single review -- WRAPPERS missing zsh's
    # precommand modifiers, then REDIRECTS missing half its own operators -- and
    # both were wrong `allow`s rather than missed prompts, because a command
    # word nobody could identify was read as "nothing runs here". A third gap
    # is a safe assumption; what it costs is now a prompt.
    in_cmd_pos = set()
    for s, e in segment_bounds(toks):
        j, _ = command_word(toks[s:e])
        if s + j >= e:
            continue          # all wrappers and keywords: this segment runs nothing
        if not re.search(r"\w", toks[s + j]) and toks[s + j] not in PUNCT_COMMANDS:
            return 0, ("a segment's command word cannot be identified, so what "
                       "runs before the delete is unknown")
        in_cmd_pos.add(s + j)
    for i, t in enumerate(toks):
        if t in LOOP_OPENERS:
            loop_starts.append((i, LOOP_CLOSER_FOR[t]))
        elif (t in LOOP_CLOSERS and loop_starts and i in in_cmd_pos
              and t == loop_starts[-1][1]):
            loop_starts.pop()
        if ENV_ASSIGN_RE.match(t):
            continue
        if t in ("function", "()") or (
                t == "(" and i + 1 < len(toks) and toks[i + 1] == ")"):
            at, reason = i, "a function definition can rebind when called"
            break
        if i not in in_cmd_pos:
            continue
        if t == "set":
            j = i + 1
            while j < len(toks) and toks[j] not in SEPARATORS:
                if toks[j] in REDIRECTS or SET_OPT_ARG_RE.match(toks[j]):
                    j += 2
                    continue
                if not SET_FLAG_RE.match(toks[j]):
                    at, reason = i, "`set` with a non-flag argument can assign"
                    break
                j += 1
            if at is not None:
                break
            continue
        words = [os.path.basename(w) for w in TOKEN_WORDS_RE.split(t) if w]
        hit = next((w for w in words
                    if w in REBINDING_BUILTINS or w == "set"), None)
        if hit is not None:
            at, reason = i, f"`{hit}` can assign to a name it computes"
            break
    if at is None:
        return None
    if loop_starts:
        return loop_starts[0][0], reason
    return at, reason


# --- names a loop binds, and where a relative operand lands -------------------

READ_VALUE_OPTS = {"-d", "-n", "-N", "-p", "-t", "-u", "-a", "-i"}
CD_OPTS = {"-L", "-P", "-e", "-@"}
GROUPING = {"(", ")", "{", "}"}


def _plain_literal(w):
    """A word with no expansion, substitution, glob, brace or masked span."""
    return not (EXPANSION_RE.search(w) or has_subst(w) or SENTINEL in w
                or any(c in w for c in GLOB_CHARS) or "{" in w)


def loop_bindings(toks):
    """-> {name: [literal words] or None} for every name a loop binds.

    None means "bound to something not in this command" -- `while read f`, or a
    `for` list built from a glob or a substitution. That is a refusal, not an
    omission: leaving such a name out of the map would restore the bare-expansion
    allow, which is the exact thing this exists to withdraw.

    A name bound twice collapses to the union of both lists, so a second loop
    cannot quietly replace a first one's values with gentler ones.
    """
    out = {}

    def bind(name, words):
        if name in out and (out[name] is None or words is None):
            out[name] = None
        elif name in out:
            out[name] = out[name] + [w for w in words if w not in out[name]]
        else:
            out[name] = words

    for i, t in enumerate(toks):
        if t in ("for", "select") and i + 1 < len(toks) and BARE_NAME_RE.match(toks[i + 1]):
            name = toks[i + 1]
            if i + 2 < len(toks) and toks[i + 2] == "in":
                words, j = [], i + 3
                while j < len(toks) and toks[j] not in SEPARATORS and toks[j] != "do":
                    words.append(toks[j])
                    j += 1
                bind(name, words if words and all(_plain_literal(w) for w in words)
                     else None)
            else:
                bind(name, None)       # `for f; do` iterates "$@"
        elif os.path.basename(t) == "read":
            j = i + 1
            while j < len(toks) and toks[j].startswith("-"):
                j += 2 if toks[j] in READ_VALUE_OPTS else 1
            while (j < len(toks) and toks[j] not in SEPARATORS
                   and BARE_NAME_RE.match(toks[j])):
                bind(toks[j], None)
                j += 1
    return out


def candidate_cwds(toks, cwd):
    """-> every directory a relative operand in this command can resolve against.

    `cd` is the only thing in a pipeline that moves the shell this rm runs in,
    and not tracking it is why `cd /; rm -rf build` cleared: `build` was judged
    against the *hook's* cwd, where it is bounded, instead of against `/`, where
    it is `/build`. A child process cannot move us, so every `cd` in the command
    text is a candidate and nothing else is.

    Both the before and the after are candidates, not only the after --
    `x && cd /tmp && rm -rf ./y` runs the rm where it started if the cd never
    happens. classify_operand judges all of them and takes the worst, which
    covers the conditional case without having to decide whether it fires.
    """
    out = [cwd]
    for start, end in segment_bounds(toks):
        argv = [t for t in toks[start:end] if t not in GROUPING]
        idx, base = command_word(argv)
        if base != "cd":
            continue
        args = [a for a in argv[idx + 1:] if a not in CD_OPTS]
        if not args:
            out.append(os.environ.get("HOME", "") or UNKNOWN_CWD)
        elif args[0] == "-" or not _plain_literal(args[0]):
            out.append(UNKNOWN_CWD)
        elif posixpath.isabs(args[0]):
            out.append(posixpath.normpath(args[0]))
        elif cwd and posixpath.isabs(cwd):
            out.append(posixpath.normpath(posixpath.join(cwd, args[0])))
        else:
            out.append(UNKNOWN_CWD)
    return list(dict.fromkeys(out))


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
    positional, named, destructive = [], [], False
    i = 1
    while i < len(argv):
        t = argv[i]
        if t in FD_EXEC_FLAGS:
            if i + 1 < len(argv) and os.path.basename(argv[i + 1]) in DESTRUCTIVE_EXEC:
                destructive = True
            break  # everything after this is the exec'd command, not a path
        head = t.split("=", 1)[0]
        if t.startswith("-"):
            if head in FD_ROOT_OPTS:
                if "=" in t:
                    named.append(t.split("=", 1)[1])
                elif i + 1 < len(argv):
                    named.append(argv[i + 1])
                    i += 2
                    continue
                i += 1
                continue
            if t in FD_VALUE_OPTS and "=" not in t:
                i += 2
                continue
            i += 1
            continue
        positional.append(t)
        i += 1
    # fd [pattern] [path...] -- the first positional is the pattern. A
    # --search-path is a root in its own right and does not displace the pattern.
    roots = named + positional[1:]
    return destructive, roots


def rsync_targets(argv):
    """rsync: -> (destructive, targets, ok, label).

    Two flags destroy, and they destroy opposite ends of the transfer:

        --delete                prunes the DESTINATION to match the source
        --remove-source-files   deletes each transferred file from the SOURCE

    The second was missed entirely, so `rsync -a --remove-source-files /home/u/
    /backup/` passed in silence while emptying /home/u.
    """
    destructive, drains_source, operands, ok = False, False, [], True
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
            if head == "--remove-source-files":
                destructive = drains_source = True
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
        ok = False  # cannot tell the source from the destination
    if not ok:
        return destructive, [], ok, ""
    if drains_source:
        return destructive, operands[:-1], ok, (
            "rsync --remove-source-files deletes each transferred file "
            "from the source")
    return destructive, operands[-1:], ok, (
        "rsync --delete prunes the destination to match the source")


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


def strip_redirections(argv):
    """-> argv without redirections. `2>/dev/null` is not three more operands.

    shlex splits a redirection into its own tokens, so a command that sends its
    noise away arrives carrying `/dev/null` -- an absolute path outside every
    safe root, and enough on its own to hold up the whole line. The rm scanner
    has dropped these since it was written; the subcommands below take their
    operands as a plain list and never did.
    """
    out, i = [], 0
    while i < len(argv):
        t = argv[i]
        if t in REDIRECTS or REDIRECT_TOKEN_RE.match(t):
            if out and out[-1].isdigit():
                out.pop()  # that was a file descriptor, not an operand
            i += 2  # the operator and its target
            continue
        out.append(t)
        i += 1
    return out


def git_clean_targets(argv):
    """git clean -f: -> (destructive, targets). Removes untracked and (with -x)
    ignored files. Unlike `git rm`, none of it is recoverable from history."""
    i, sub = git_subcommand(argv)
    if sub != "clean":
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


def git_subcommand(argv):
    """-> (index, name) of git's subcommand, past git's own options."""
    idx, _ = command_word(argv)
    i = idx + 1
    while i < len(argv) and argv[i].startswith("-"):
        # git's own options, some of which eat the next token (`git -C /path`).
        if argv[i] in GIT_VALUE_OPTS and "=" not in argv[i]:
            i += 2
            continue
        i += 1
    return (i, argv[i]) if i < len(argv) else (i, "")


def git_worktree_targets(argv):
    """git worktree remove: -> (destructive, targets, forced).

    With --force it deletes the worktree directory outright, untracked files
    included -- the property that puts `git clean` in scope and keeps `git rm`
    out of it. Without it, git declines every worktree that holds anything
    unrecoverable, so the flag is the part worth reporting.
    """
    i, sub = git_subcommand(argv)
    if sub != "worktree":
        return False, [], False
    rest = argv[i + 1:]
    if not rest or rest[0] != "remove":
        return False, [], False
    forced, paths = False, []
    for t in rest[1:]:
        if t.startswith("--"):
            if t == "--force":
                forced = True
        elif t.startswith("-") and len(t) > 1:
            if "f" in t:
                forced = True
        else:
            paths.append(t)
    return True, paths, forced


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

# The non-rm commands this file knows how to reason about. Used to find one
# inside a segment when command_word cannot.
HANDLED = {"find", "fd", "fdfind", "rsync", "rclone", "ssh"} | GIT_NAMES


def analyse_segment(argv, cwd, depth, assigned=None, loops=None,
                    may_clear=False):
    """-> [Finding] for the non-rm commands in one segment.

    may_clear is decided per SEGMENT by analyse, because that is the unit a
    rebinding can sit before or after; every target found in here shares it.
    """
    out = []
    segment = argv
    idx, base = command_word(argv)
    # command_word steps over a wrapper one token at a time, so a wrapper that
    # takes its OWN option argument leaves it standing where the command should
    # be: `sudo -u root find / -delete` stopped at `-u`, `nice -n 5` at `-n`,
    # and `timeout` was never a wrapper at all. Every one of those passed in
    # silence. rm never had this problem because it is scanned across all
    # tokens; this is the same fallback for everything else. A guarded name
    # appearing as a mere argument costs a prompt at worst, and only when a
    # destructive flag sits beside it.
    if base not in HANDLED:
        for j, t in enumerate(argv):
            if os.path.basename(t) in HANDLED:
                idx, base = j, os.path.basename(t)
                break
    argv = argv[idx:]
    if not argv:
        return out

    def rate(targets, label, missing, bare_var_ok=False):
        """Judge each target, tagging every Finding with the same why-this-one label."""
        if not targets:
            out.append(Finding("ask", segment, label, None, missing))
            return
        for t in targets:
            d, r = classify_operand(t, cwd, bare_var_ok=bare_var_ok,
                                    assigned=assigned, loops=loops,
                                    may_clear=may_clear)
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
        destructive, targets, ok, label = rsync_targets(argv)
        if destructive and not ok:
            out.append(Finding("ask", segment,
                               "rsync deletes at one end of this transfer", None,
                               "the source and destination could not be told apart"))
        elif destructive:
            rate(targets, label, "no operand to delete from")
    elif base == "rclone":
        destructive, targets, sub = rclone_targets(argv)
        if destructive:
            label = ("rclone %s prunes the destination to match the source" % sub
                     if sub in ("sync", "move")
                     else "rclone %s destroys what it names" % sub)
            rate(targets, label, f"rclone {sub} with no operand")
    elif base in GIT_NAMES:
        gargv = strip_redirections(argv)
        destructive, paths = git_clean_targets(gargv)
        label = ("git clean removes untracked and ignored files, "
                 "which are not in history and cannot be restored")
        worktree = forced = False
        if not destructive:
            destructive, paths, forced = git_worktree_targets(gargv)
            worktree = destructive
            label = ("git worktree remove deletes the worktree directory, "
                     "untracked files included")
        if worktree and not forced:
            # Bounded by git rather than by the path, which is why no operand is
            # judged here: `remove` declines a worktree holding an untracked or
            # modified file, and declines a path that is not a registered
            # worktree at all. There is no spelling of the operand that turns
            # this into an rm -rf -- an unset variable is a usage error, not a
            # delete. Only ignored files can go, and only from inside a worktree.
            out.append(Finding("allow", segment, label,
                               paths[0] if paths else None,
                               "git refuses to remove a worktree holding "
                               "anything unrecoverable without --force"))
        elif destructive:
            for t in paths:
                # bare_var_ok stays False here, and for worktree remove that
                # is knowingly conservative rather than true: with no operand
                # git prints its usage and with an empty one it says `'' is not
                # a working tree`, so `remove --force "$W"` cannot become a
                # delete and the reason it gets ("falls back to the current
                # directory") describes git clean, not this. Setting it to
                # `worktree` was measured -- 9 more allows across 579 real
                # verdicts, against one corpus command moving ask -> allow, so
                # it costs a fixture regeneration to buy almost nothing. Left
                # here rather than done, and the price is recorded so the next
                # person does not have to measure it again.
                d, r = classify_operand(t, cwd, bare_var_ok=False,
                                        assigned=assigned, loops=loops,
                                        may_clear=may_clear)
                # git clean runs in the tree you are standing in, and untracked
                # and ignored files are not in history: even a "safe" path only
                # earns a prompt there, never a silent pass, and saying "literal
                # relative path" would read like a clean bill. A forced worktree
                # remove is already bounded to one registered worktree, so it
                # keeps the ordinary path verdict instead.
                if d == "allow" and not worktree:
                    d, r = "ask", ("the path itself is bounded, but nothing "
                                   "under it is recoverable")
                out.append(Finding(d, segment, label, t, r))
    elif base == "ssh":
        # assigned is deliberately NOT passed into the recursion. The local shell
        # expands a double-quoted remote command before ssh runs, and shlex has
        # stripped the quotes by now, so `ssh h "rm -rf $W/*"` and
        # `ssh h 'rm -rf $W/*'` are indistinguishable here. analyse() re-reads
        # the remote string's own bindings instead.
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
    try:
        toks = tokenize(cmd)
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

    def segment_start(i):
        """Where that segment begins, for deciding whether it ran before a
        rebinding. The segment's own start and not token i, because a construct
        sitting between the two is in this very command and has not run yet."""
        for s, e in bounds:
            if s <= i < e:
                return s
        return 0

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

    # Consulted to escalate from anywhere, and to clear only before rebind_at;
    # see classify_one.
    assigned = proven_bindings(cmd)
    # Where the map stops being provable. Computed once over the whole token
    # stream rather than per segment, because the answer is a property of the
    # LINE -- the first construct that could rebind a name without writing it --
    # and asking each segment separately would only rediscover it len(bounds)
    # times. A segment starting before it ran with the bindings still intact.
    rebind = _rebinding_possible(toks, cmd)
    rebind_at = len(toks) if rebind is None else rebind[0]
    # These two do clear as well as condemn, and both earn it the same way: a
    # loop's word list and a literal `cd` target are complete and visible right
    # here, where an assignment map's binding may already have been overwritten.
    loops = loop_bindings(toks)
    cwds = candidate_cwds(toks, cwd)

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
        may_clear = segment_start(i) < rebind_at
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
            d, r = classify_operand(op, cwds, assigned=assigned, loops=loops,
                                    may_clear=may_clear)
            # No label: for rm every operand is a target, which the echoed
            # command already makes obvious.
            findings.append(Finding(d, segment, None, op, r))

    # --- everything else, one segment at a time.
    for start, end in bounds:
        findings.extend(
            analyse_segment(toks[start:end], cwds, depth, assigned, loops,
                            start < rebind_at))

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

def clip(s, n=MAX_TOKEN):
    return s if len(s) <= n else s[:n - 1] + "…"


MARK_RE = re.compile("%s([^%s]+)%s" % (MARK, MARK, MARK))


def unmark(reason):
    """Drop a reason's markers, clipping the span they enclosed.

    The markers used to select what got painted. They outlive the colour for
    two reasons: a sentinel must never reach the dialog either way, and the
    span they bound is the one part of a bail-out that can be arbitrarily long
    -- `<a whole quoted script> runs in command position` -- so it is still the
    span that needs clipping.
    """
    return MARK_RE.sub(lambda m: clip(m.group(1)), reason)


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
    pieces = [clip(t) for t in toks]
    if sum(len(p) + 1 for p in pieces) > MAX_ECHO:
        # Too long to show whole. The command, its flags and every flagged
        # operand always survive; runs of anything else collapse to one ellipsis.
        head = command_head(toks)
        kept, elided = [], False
        for i, p in enumerate(pieces):
            if i < head or toks[i] in flagged or p.startswith("-"):
                kept.append(p)
                elided = False
            elif not elided:
                kept.append("…")
                elided = True
        pieces = kept
    return " ".join(pieces)


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
        # Nothing to point at, so the marked span is all the verdict can name.
        return f"{headline} -- {unmark(findings[0].reason)}"
    out = [headline]
    for segment, label, group in blocks(findings):
        out.append("")
        if segment:
            lit = {f.operand for f in group if f.operand}
            lit.update(t for f in group for t in f.highlight)
            out.append("  " + echo_line(list(segment), lit))
            if label:
                out.append("  " + label)
            out.append("")
        ops = [clip(f.operand) for f in group if f.operand]
        width = max((len(o) for o in ops), default=0)
        for f in group:
            if not f.operand:
                out.append("  " + unmark(f.reason))
                continue
            # The row leads with the operand so it keys back to the echo above,
            # which is the whole of what colour used to do here. Padding lines
            # the reasons up into a column.
            op = clip(f.operand)
            out.append("  " + op + " " * (width - len(op) + 2)
                       + unmark(f.reason))
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
        # An allow reaches the whole tool call, so it is withheld from a line
        # carrying something this guard never examined. See RISKY_NEIGHBOURS.
        if risky_neighbour(cmd):
            passthrough()
        # Nothing was blocked, so there is nothing to point at. Stay on one line.
        emit("allow", "destructive command on a provably safe target -- "
                      f"{unmark(findings[0].reason)}")
    # Only the operands that actually held the command up. A proven-safe operand
    # sitting next to a dangerous one is noise in a prompt about the dangerous one.
    blocked = [f for f in findings if f.decision != "allow"]
    if worst == RANK["deny"]:
        # A deny and an ask are told apart by their wording, which used to be
        # backed by painting this headline and leaving the ask's plain. The
        # wording now carries it alone: "refusing" against "needs confirmation".
        emit("deny", render("refusing catastrophic delete", blocked))
    emit("ask", render("destructive recursive command needs confirmation", blocked))


if __name__ == "__main__":
    main()
