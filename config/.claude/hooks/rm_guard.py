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
    git clean               only with a relocated work tree -- see below
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
    rg                  read-only. `-x` is --line-regexp and `-r` is --replace
                        (rewrites printed output, never the file). A rule here
                        would only collide with fd's exec flags and misfire.
    dd, diskutil        catastrophic but device-shaped, not path-shaped. They
                        need a different check than a path classifier.

Git is left alone: no ask, and no allow either. Its deletions are bounded by git
rather than by their operand, so there is no path to judge -- verified against
git 2.56:

    git clean           only untracked files inside the repository it runs in.
                        `git clean -f /etc` is "outside repository", not a wipe.
    git worktree remove only a registered worktree. Anything else is "not a
                        working tree", --force or not.
    git rm              only tracked files, all of them recoverable from history.

Judging those paths anyway asked on 84 real commands and protected nothing; in
Codex, where an ask becomes a refusal, it stopped 84 tasks. Not an allow, because
an allow approves the whole call: it was tried, and `man git clean && curl -X
DELETE ...` came back approved on the strength of a word.

The exception is a git clean whose tree has been MOVED, which still asks.
--work-tree and GIT_WORK_TREE make it any path; --git-dir or GIT_DIR alone make
it the cwd whether or not that is a repository; `git config core.worktree`
persists a path for the next clean; and `git init` makes the cwd a repository
with nothing tracked. Any of those turns `git clean -fdx` into rm -rf of a
directory git never bounded -- `git --work-tree=/ clean -fdx` is rm -rf / minus
the tracked files. No real command has done it.

That check is per line, so it cannot see a relocation made by an earlier tool
call -- a `git config core.worktree /` alone, then a clean. Over-asking is the
known cost the other way: `git rev-parse --git-dir && git clean -fdx` asks.

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
UNBOUND ${W:?} -- one the line assigns a value the parser cannot read
included -- still clears with no path check, deliberately: the guarded form
is the assertion of intent this guard's own advice says to write, there is
nothing in the command to check it against, and distrusting it would cost a
prompt on exactly the command the author hardened. A value the parser CAN read
-- $HOME, ~, $PWD, a name the line proves -- is judged as the path it names,
and one naming a critical directory it cannot place ($TMPDIR, $OLDPWD) asks.

Two passes hold for the same reason. A bare "$d" whose value the parser cannot
read clears as base did, since an empty one only errors; "$d"/x and "$d"/* still
ask. An unguarded $PWD/x is ./x: `zsh -c` always sets PWD, so ${PWD:?} adds
nothing there.

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

    hidden cwd movers          where a relative operand lands is read off the
                               cd (chdir in zsh), pushd and popd on the line,
                               a trap action that holds one, and zoxide's z,
                               zi, __zoxide_z and __zoxide_zi, which the shell
                               snapshot defines and which move the shell to a
                               place no text names. A shell function, a trap or
                               an alias from an earlier tool call can move it
                               too, with nothing on the line to show it. zsh's
                               AUTO_CD is not read: it applies only to a zsh
                               reading commands from stdin, and Claude Code
                               runs `zsh -c`.

    a word segment_bounds      shlex glues punctuation, and segment_bounds
    does not start             splits only on SEPARATORS, so a command word
                               after a glued `);`, `;(` or `));`, after `|&`
                               or zsh's `repeat N do`, or inside a one-line
                               function body is no segment's command word.
                               An expansion there -- `B=rm; true |& $B -rf /`
                               -- is neither bailed on nor resolved, and the
                               line may be silent; a literal `rm` there is
                               read, since the rm scan reads every token.
                               Splitting glued tokens in tokenize closes it,
                               and touches every reading.

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
segment too -- `eval` alone says which word stopped the guard and nothing about
what eval was told to run, so what it hides is echoed alongside it.

The verdict is plain text. Claude Code renders permissionDecisionReason through
a component that strips control characters, so nothing here may rely on colour;
see the note above MAX_ECHO for what that cost and why it is not coming back.
"""
import json
import os
import posixpath
import pwd
import re
import shlex
import stat
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
# The zsh form -- `$~W` globs W's value, `$=W` splits it into words, `$^W`,
# `$+W` and `$#W` -- sits before the special parameters, so `$#W` is one
# expansion rather than `$#` and a literal W. Missing it, `$~W` read as a
# relative path and `W='/*'; rm -rf $~W` cleared. The flags apply to a
# positional, a special parameter or a braced name as well: `$~1`, `$~@`,
# `$=1` and `$~{W}` read as literals until they were listed too.
EXPANSION_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*"
                          r"|\$[~=^+#]+(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+"
                          r"|[@*#?$!-]|\{[^}]*\})"
                          r"|\$[0-9@*#?$!-]")
# The zsh form alone, for naming it in a verdict.
ZSH_FLAG_EXPANSION_RE = re.compile(r"^\$[~=^+#]+[A-Za-z_0-9@*#?$!{-]")
# A word that is nothing but expansions -- `$X`, `${=X}`, `$1`, `$@` -- and so
# names no command at all until the shell runs. `$S/bin/x` is not one.
EXPANSION_WORD_RE = re.compile(r"^(?:%s)+$" % EXPANSION_RE.pattern)
BARE_VAR_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
# An expansion glued through a quote or a backslash to a word character:
# `"$W"usr`, `"${W}"x`, `$W"usr"`, `"${W:?}"x`. shlex removes the quotes and
# hands back `$Wusr` -- a bare variable, Wusr, which nothing set -- while the
# shell expands W and glues usr on, so `W=/; rm -rf "$W"usr` is rm -rf /usr.
# A hit anywhere on the line turns off both free passes for it.
QUOTE_GLUE_RE = re.compile(r"(?:%s)[\"'\\]+\w" % EXPANSION_RE.pattern)
GLUED_REASON = ("an expansion on this line is glued through a quote to the word "
                "after it, as in \"$W\"usr, so the name read here is not the "
                "one the shell expands")
# The variable name inside any expansion form: $W, ${W}, ${W:-default}.
EXP_NAME_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)")
# The inside of ${...} that names one variable, with an operator and a word
# or not: N, N:-word, N-word, N:=word, N::=word, N:+word, N:?msg.
BRACED_REF_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:(::=|:[-?+=]|[-?+=])(.*))?$", re.S)
NAME_IN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PLAIN_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def value_refs(text):
    """-> [(start, end, kind, name, op, word)] for each expansion in an
    assignment's value, ${...} braces matched so a word may nest one, or None
    when they do not balance. kind is "ref" -- $N or ${N}, with an operator
    and its word or not -- "indirect" for zsh's ${(P)N} and bash's ${!N},
    which name the variable they read by its value, or "other": ${#N}, a
    modifier, zsh's $N:h and $N[1], a positional or a special parameter.
    name is the variable an "other" mentions, for the critical-name test."""
    out, i = [], 0
    while True:
        j = text.find("$", i)
        if j < 0:
            return out
        if text.startswith("${", j):
            depth, e = 0, j
            while e < len(text):
                if text.startswith("${", e):
                    depth, e = depth + 1, e + 2
                    continue
                if text[e] == "}":
                    depth -= 1
                    if not depth:
                        break
                e += 1
            if depth:
                return None
            inner = text[j + 2:e]
            m = BRACED_REF_RE.match(inner)
            if inner[:1] in ("(", "!"):
                out.append((j, e + 1, "indirect", None, None, None))
            elif m:
                out.append((j, e + 1, "ref") + m.groups())
            else:
                n = NAME_IN_RE.search(inner)
                out.append((j, e + 1, "other", n and n.group(0), None, None))
            i = e + 1
            continue
        m = PLAIN_REF_RE.match(text, j)
        if m:
            kind = "other" if text[m.end():m.end() + 1] in ("[", ":") else "ref"
            out.append((j, m.end(), kind, m.group(1), None, None))
            i = m.end()
            continue
        m = EXPANSION_RE.match(text, j)
        end = m.end() if m else j + 1
        n = NAME_IN_RE.search(text[j:end])
        out.append((j, end, "other", n and n.group(0), None, None))
        i = end
# An operand that starts with the working directory and goes on below it:
# `$PWD/x`, `${PWD}/x`, `${PWD:?}/x`. Group 1 is the rest, from its `/`.
PWD_PREFIX_RE = re.compile(r"^\$(?:PWD|\{PWD(?::\?[^}]*)?\})(/.*)$", re.S)
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
# message argument, which does execute and is no longer read, by the raw-text
# heuristics or by the substitution re-read in _analyse. That is a way to
# type a deletion, not a way to make one by accident, and the accident is what
# this guard is for. It is not approved either: a destructive one withdraws
# the line's allow, so normal permissions decide.
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
# Constructs whose argument is code the guard cannot see: the bail-out set.
OPAQUE = {"eval", "source", "."}
# What _indirection_present refuses: OPAQUE plus exec and xargs. Neither can
# rebind the parent shell, but both were refused with the rest when this was
# one set, and narrowing the bail-out must not also loosen proven_bindings.
INDIRECTION = OPAQUE | {"exec", "xargs"}
# The operand xargs supplies from its input, standing where xargs puts it:
# after the command's own arguments, or in place of each -I string inside
# one. classify_one refuses it before anything else. It is a plain word on
# purpose: an argument list that is joined and read again -- ssh's remote
# command -- must still hold it as one word, where `<xargs-input>` would come
# back as two redirections and an operand would vanish. A real word equal to
# it costs a prompt and nothing more.
XARGS_INPUT = "{xargs-input}"
# GNU xargs plus the BSD subset. Short options that take a value (attached or
# as the next word), take none, or take an OPTIONAL attached value (-i, -e, -l).
# An option missing here makes the xargs unreadable, which asks.
XARGS_FLAGS = set("0rtpxo")
XARGS_VALUE = set("adEILnPs")
XARGS_OPTIONAL = set("iel")
XARGS_LONG_VALUE = {"--arg-file", "--delimiter", "--max-args", "--max-procs",
                    "--max-chars", "--process-slot-var"}
XARGS_LONG_FLAGS = {"--null", "--no-run-if-empty", "--verbose", "--interactive",
                    "--exit", "--open-tty", "--show-limits", "--help", "--version"}
XARGS_LONG_OPTIONAL = {"--replace", "--eof", "--max-lines"}
# The long option each short one spells, where it has one, so parse_xargs can
# ask one question of both: -i is --replace, -n --max-args, -l --max-lines.
XARGS_LONG_NAME = {"i": "--replace", "n": "--max-args", "l": "--max-lines",
                   "e": "--eof", "a": "--arg-file", "d": "--delimiter",
                   "P": "--max-procs", "s": "--max-chars"}
# A count as GNU xargs reads one (strtol, base 10). Only one that comes out
# as 1 leaves an earlier -I standing; see parse_xargs.
XARGS_COUNT_RE = re.compile(r"^[ \t\n\v\f\r]*[+-]?[0-9]+$")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
# A shell's own options, so the script after `-c` can be found. A short bundle
# containing `c` is how `bash -lc` and `sh -ec` spell it. Each `o` or `O` in a
# bundle takes the next word as its option name: `-euo pipefail -c SCRIPT`.
SHELL_FLAG_RE = re.compile(r"^[-+][A-Za-z]+$")
# The long options known to take no value. --version and --help run nothing.
SHELL_LONG_FLAGS = {"--login", "--noprofile", "--norc", "--posix",
                    "--restricted", "--verbose", "--no-rcs", "--version", "--help"}
# A word that may be the shell's -c, found past a point where its options
# stopped being readable.
SHELL_C_FLAG_RE = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")
# fish's ways to be handed a script: -c/--command, and -C/--init-command, which
# runs before anything else. fish is not POSIX shell, so none of it is read.
FISH_SCRIPT_FLAG_RE = re.compile(r"^-[A-Za-z]*[cC][A-Za-z]*$|^--(?:init-)?command(?:=|$)")
GIT_NAMES = {"git"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                  "--exec-path", "--config-env"}
# Words that prefix a command without changing which command it is.
#
# The first six spawn a subprocess, so they cannot rebind the calling shell and
# were only ever about naming the right command. The next three are zsh
# PRECOMMAND MODIFIERS -- they run the command IN the current shell, which makes
# their absence a wrong `allow` rather than a cosmetic miss:
#
#     W=/tmp/build; builtin typeset -g "$(printf '\127')=/"; rm -rf "$W"/*
#
# came back allow and runs as `rm -rf //*`. `builtin` was, word for word, the
# wrapper _rebinding_possible's docstring claimed could not hide a builtin from
# it. noglob and nocorrect do the same job.
#
# exec runs the command in place of the shell. It was a bail-out, which asked
# on every line it led, and a wrapper says which command it is: `exec rm -rf
# /` is that rm. With no command left -- `exec 3>/dev/null` -- it runs nothing.
# timeout, stdbuf and setsid spawn the command after their own options, and
# reading their first argument as the command hid an expansion that IS one:
# `X=...; rm -rf ./b; timeout 5 $X` cleared on the strength of the ./b.
WRAPPERS = {"sudo", "command", "env", "nohup", "time", "nice",
            "builtin", "noglob", "nocorrect", "exec", "timeout", "stdbuf",
            "setsid"}
# A wrapper's own options, so command_word can step past them to the command
# they run: (flag letters, letters that take a value, long options that take
# one -- as `--opt=v` or as the next word -- and long options that take none).
# Stopping on the option instead hid the command: `env -i $X` read `-i` as the
# command and never saw that `$X` was one. An option missing here stops
# command_word on it, and a line with a delete on it asks. env's -S and
# --split-string are left out on purpose: the string they take IS the command
# and its arguments. So are sudo's -h and --host, which take the next word
# only when it is neither an option nor an assignment.
WRAPPER_OPTS = {
    "sudo": ("ABbEeHiKklNnPSsVv", "aCcDgpRrTtUu",
             {"--auth-type", "--chdir", "--chroot", "--close-from",
              "--command-timeout", "--group", "--login-class", "--other-user",
              "--prompt", "--role", "--type", "--user"},
             {"--askpass", "--background", "--bell", "--edit", "--help",
              "--list", "--login", "--non-interactive",
              "--preserve-env", "--preserve-groups", "--remove-timestamp",
              "--reset-timestamp", "--set-home", "--shell", "--stdin",
              "--validate", "--version"}),
    "env": ("iv0", "aCu", {"--argv0", "--chdir", "--unset"},
            {"--block-signal", "--debug", "--default-signal", "--help",
             "--ignore-environment", "--ignore-signal", "--list-signal-handling",
             "--null", "--version"}),
    "nice": ("", "n", {"--adjustment"}, {"--help", "--version"}),
    # GNU time's options; bash's keyword knows only -p, and runs `-f` as the
    # command, which fails -- reading past it is then only stricter.
    "time": ("apqvV", "fo", {"--format", "--output"},
             {"--append", "--help", "--portability", "--quiet", "--verbose",
              "--version"}),
    "command": ("pvV", "", set(), set()),
    # bash's and zsh's exec: -c empties the environment, -l makes a login
    # process, -a sets argv[0].
    "exec": ("cl", "a", set(), set()),
    "timeout": ("v", "ks", {"--kill-after", "--signal"},
                {"--foreground", "--help", "--preserve-status", "--verbose",
                 "--version"}),
    "stdbuf": ("", "eio", {"--error", "--input", "--output"},
               {"--help", "--version"}),
    "setsid": ("cfhVw", "", set(),
               {"--ctty", "--fork", "--help", "--version", "--wait"}),
}
# Words a wrapper takes after its options and before the command, however they
# look: timeout's duration, so `timeout 5 rm` runs rm and not a command named
# 5. Each wrapper here stops reading options at its first such word, as GNU
# getopt does under the `+` these tools use: `timeout 5 -s KILL x` runs `-s`.
# One holding an expansion may split into several words and move the command,
# so command_word_detail treats it as an option it cannot read.
WRAPPER_POSITIONALS = {"timeout": 1}
# The wrapper options above that run the command in another directory, so a
# relative operand no longer resolves against the shell's cwd: `env -C /
# rm -rf ./usr` is rm -rf /usr. (letters, long options)
WRAPPER_CHDIR = {"env": ("C", {"--chdir"}),
                 "sudo": ("DR", {"--chdir", "--chroot"})}
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
    "-J", "-W", "-S", "-w", "-Q", "-E", "-I", "-B", "-O", "-P",
}
# ...the same, as the letters of a bundle: `ssh -tp 22 h`.
SSH_VALUE_CHARS = {o[1] for o in SSH_VALUE_OPTS}
# A remote spec: user@host:path, host:path, or a URL. Never a local path.
REMOTE_SPEC_RE = re.compile(r"^[A-Za-z0-9_.+-]*(?:@[A-Za-z0-9_.-]+)?:|^[a-z][a-z0-9+.-]*://")

RANK = {"allow": 0, "ask": 1, "deny": 2}
# How many times one command may hand text to another reader -- an ssh far
# end, a `sh -c` script, a heredoc fed to a shell, a sourced file -- before
# the guard stops following and asks.
MAX_DEPTH = 3
# How many reads one assigned value may take, through the names it reads and
# theirs, before the guard stops following and refuses the pass. Assignments
# that read each other in a loop may otherwise branch into millions.
READ_BUDGET = 200
# Set by --no-disk (see main). Turns off every rule that reads the filesystem,
# so the corpus fixture replays hermetically. A flag, never an environment
# variable: Claude Code runs this hook with no arguments, so nothing in a
# session can switch it on.
NO_DISK = False
# Fix 3: the largest sourced file read in place.
MAX_SOURCED_BYTES = 64 * 1024
# Reserved words that open a construct GROUPING_WORDS does not list, refused
# in a sourced file for the same reason: left unfinished, it is dropped.
SOURCED_OPENERS = {"!", "time", "[[", "coproc"}
# `. FILE` or `source FILE` leading the line, FILE a plain word. The charset
# excludes every quote, escape, expansion and glob, so this reading and
# shlex's cannot disagree about what FILE is -- the token is also compared.
SOURCE_LEAD_RE = re.compile(
    r"[ \t]*(?:\.|source)[ \t]+([A-Za-z0-9_./~+@:,%=-]+)[ \t]*(;|&&|\n|$)")

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
# A word that starts with an expansion or a substitution, then whitespace and a
# recursion-shaped flag: `${X:-rm} -rf /`, `"$X" -rf /`, `$(printf rm) -rf /`.
# It names nothing GUARDED_CMD_RE can match, so the raw-text early exits in
# _analyse returned before a -c script, a heredoc fed to a shell or a
# substitution's body holding one was ever read. Matched over the raw line,
# heredoc bodies and all, and crudely, because a hit only makes the line
# dangerous: it costs a prompt at most.
EXPANSION_FLAG_RE = re.compile(
    r"(?:^|(?<=[\s;&|(){}'\"`!]))"
    r"(?:%s|\$\([^()]*\)|`[^`]*`)" % EXPANSION_RE.pattern
    + r"[^\s;&|<>]*(?:\s|\\\n)+"
    r"(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive|--no-preserve-root)(?![\w-])")
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
# `clean` as git's subcommand, not the word anywhere on a line that runs git.
# The bare word made `git diff --quiet && echo clean` and `make clean` danger,
# and a `source` or `bash -c` beside them then asked on 53 real commands that
# were never a git clean. No separator may sit between the two words, which is
# GIT_RM_RE's rule with a token class wide enough for `-C "$S/repo"`.
GIT_CLEAN_RE = re.compile(
    r"(?<![\w-])git(?![\w-])(?:[ \t]+[^\s;&|]+){0,8}?[ \t]+clean(?![\w-])")
# Anything that moves git clean's work tree off the repository it would find on
# its own -- see the module docstring and git_relocates. Config keys are
# case-insensitive to git, so `git config Core.WorkTree /` is the same key.
# `gitdir:` is a .git FILE pointing elsewhere, which makes its directory a work
# tree exactly as `git init` does.
GIT_RELOCATE_RE = re.compile(
    r"(?<![\w-])(?:--work-tree|--git-dir|GIT_WORK_TREE|GIT_DIR)(?![\w-])"
    r"|(?i:core\.worktree)|(?<![\w-])gitdir:")
GIT_CLEAN_LABEL = ("git clean removes untracked and ignored files, "
                   "which are not in history and cannot be restored")
GIT_RELOCATED = ("the work tree is relocated (--work-tree, --git-dir, GIT_DIR, "
                 "core.worktree, gitdir: or git init), so git clean is bounded "
                 "by that path and not by a repository")
# ...and the same, per token after the shell has removed its quoting. An option
# held in a variable (`OPT=--work-tree=/`) counts too, since `git $OPT clean` is
# how it would be spent.
GIT_RELOCATE_TOKEN_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=)?--(?:work-tree|git-dir)(?:=|$)"
    r"|^(?:GIT_WORK_TREE|GIT_DIR)="
    r"|(?:^|=)core\.worktree(?:=|$)", re.I)

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

# Fix 1: commands whose arguments are only ever printed or matched, never run.
# sed and awk are absent on purpose -- GNU sed's `e` and awk's system() both
# execute text.
INERT_READERS = {"grep", "egrep", "fgrep", "rg", "echo", "printf"}
# ...and what a line of them may pipe into or be mixed with. sort, uniq and
# less are absent on purpose: each can write a file or run a program.
SAFE_SINKS = {"head", "tail", "wc", "cut", "tr", "grep", "egrep", "fgrep", "rg",
              "cat", "column", "nl"}
# The builtins that move the shell to a directory they name. zsh's chdir is
# its cd under another name, and Claude Code's shell is zsh.
CD_WORDS = ("cd", "chdir", "pushd", "popd")
INERT_COMMANDS = INERT_READERS | SAFE_SINKS | {"cd", "chdir", "ls", "pwd", "true"}
# Words that open or close a group, loop, function or coprocess. A pipe or
# redirect on one sits outside every segment, so none may appear at all.
GROUPING_WORDS = {"(", ")", "{", "}", "do", "done", "then", "fi", "else", "elif",
                  "esac", "case", "if", "while", "until", "for", "select",
                  "function", "coproc", "foreach", "end", "repeat", "alias"}
PUNCT_ONLY_RE = re.compile(r"^[^A-Za-z0-9_]+$")


def danger_hint(text):
    """Does the raw text plausibly contain a destructive invocation?"""
    return any(name.search(text) and flag.search(text)
               for name, flag in DANGER_PAIRS)


def _is_boundary(prev):
    """Can a `#` straight after the output element `prev` start a comment?

    An escape pair is one 2-character element, `\\ ` for an escaped space; its
    last character is a space but it is part of a word, never a boundary.
    """
    if len(prev) == 2 and prev[0] == "\\":
        return False
    return prev[-1:] in COMMENT_BOUNDARY


def _line_comment_before(text, pos):
    """Does a comment open on pos's line before pos? Conservative: a `#` in
    quotes there counts too."""
    line = text[text.rfind("\n", 0, pos) + 1:pos]
    return any(c == "#" and (i == 0 or line[i - 1] in COMMENT_BOUNDARY)
               for i, c in enumerate(line))


def _heredoc_opener_rest(m):
    """What follows a quoted heredoc's delimiter on its OPENING line, plus the
    newline that ends that line.

    HEREDOC_RE matches from `<<` through the terminator, so replacing the whole
    match also erased the code after the delimiter: `cat <<'EOF' >/dev/null;
    rm -rf /` followed by a body was masked to `cat` and nothing else, and the
    guard went silent on it. That rest of the line runs before the body is
    read, and it goes back as code. HEREDOC_RE's `^` anchor guarantees a
    newline between the delimiter and the terminator.
    """
    start = m.end(3) + 1                  # past the delimiter's closing quote
    nl = m.string.index("\n", start)
    return m.string[start:nl] + "\n"


def _opener_continues(rest):
    """Does a heredoc's opening line, `rest`, leave the LOGICAL line unfinished?

    Bash reads the body from the line after the opening LOGICAL line ends. If
    the remainder leaves a quote, a backtick or a paren open, or ends in an
    unescaped backslash, the physical lines below are still code -- the body
    starts later than HEREDOC_RE assumes. Masking them hides code:

        cat <<'EOF'; ssh h "        the masked span re-balances the quote into
        rm -rf /                    an empty string, and bash runs `rm -rf /`
        EOF                         on the far end
        "; rm -rf ./build

        cat <<'EOF' && \\           bash runs `cat <<'EOF' && rm -rf /`
        rm -rf /
        EOF

    `rest` is without its trailing newline. An unquoted `#` at the start of a
    word ends the scan: an apostrophe in a comment opens nothing. An escaped
    character is never a boundary: in `x\\ #'` the `#` is mid-word.
    """
    quote, depth, i, n = None, 0, 0, len(rest)
    escaped_to = -1                       # index just past the last escape
    while i < n:
        c = rest[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\":
            if i + 1 >= n:
                return True
            escaped_to = i = i + 2
            continue
        if quote is None:
            if (c == "#" and i and i != escaped_to
                    and rest[i - 1] in COMMENT_BOUNDARY):
                break
            if c in "'\"`":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")" and depth:
                depth -= 1
        elif quote == '"':
            if c == '"':
                quote = None
            elif c == "$" and rest[i + 1:i + 2] == "(":
                depth += 1
                i += 1
            elif c == ")" and depth:
                depth -= 1
        elif quote == "`" and c == "`":
            quote = None
        i += 1
    return quote is not None or depth > 0


def _masked_heredocs(text):
    """Yield (placeholder, match) for each heredoc safe to mask.

    One whose opening line continues is left out: it stays in the raw text, so
    the tokenizer sees the real structure, or fails to parse, which asks.
    """
    k = 0
    for m in HEREDOC_RE.finditer(text):
        if (_opener_continues(_heredoc_opener_rest(m)[:-1])
                or _line_comment_before(text, m.start())):
            continue
        yield "%sHD%d%s" % (SENTINEL, k, SENTINEL), m
        k += 1


def _heredoc_skipped(cmd):
    """Did _masked_heredocs skip any heredoc on this line?

    Leaving a skipped heredoc in the raw text is not conservative. Its body is
    prose to bash, so it can hold an odd number of quotes that pair with a quote
    later on the line and hide code from the tokenizer: the tokenizer then
    reads real code as quoted data. What is body and what is code cannot be
    told apart, so the caller asks instead.
    """
    cmd = cmd.replace(SENTINEL, "").replace(MARK, "")
    found = sum(1 for _ in HEREDOC_RE.finditer(cmd))
    return found != sum(1 for _ in _masked_heredocs(cmd))


def _heredoc_body(m):
    """A HEREDOC_RE match's body: everything between its opening line and its
    terminator line."""
    s = m.group(0)
    return s[s.index("\n") + 1:s.rindex("\n")]


def heredoc_bodies(cmd):
    """-> {placeholder: body} for each heredoc mask_opaque masks.

    Built from _masked_heredocs over the same cleaned text, so the placeholders
    are mask_opaque's own -- one reading, not a second one.
    """
    cmd = cmd.replace(SENTINEL, "").replace(MARK, "")
    return {ph: _heredoc_body(m) for ph, m in _masked_heredocs(cmd)}


def unread_destructive_heredoc(cmd, read=()):
    """Does a masked heredoc body read as a destructive command that nothing
    here read as a script?

    A masked body is data to every rule in this file, but something on the
    line may still run it: `ssh h <<'EOF'`, `docker exec -i c sh <<'EOF'`.
    An allow approves the whole call, and with it that body. A body inside a
    message-flag value is a commit or PR message, which strip_data already
    treats as data, and is skipped. `read` names placeholders a rule DID
    read as a script.
    """
    cmd = cmd.replace(SENTINEL, "").replace(MARK, "")
    msgs = [m.span() for m in MSG_FLAG_RE.finditer(cmd)]
    for ph, m in _masked_heredocs(cmd):
        if ph in read or any(s <= m.start() < e for s, e in msgs):
            continue
        if danger_hint(_heredoc_body(m)):
            return True
    return False


def strip_data(text):
    """Blank the regions of a command line the shell treats as data, not code.

    Only the raw-text heuristics read this. Tokenizing runs on the original
    command, so the per-command analysis below still sees a message flag's
    value -- mask_opaque covers the one region that has to be hidden from the
    tokenizer too, because prose in a heredoc body breaks shlex outright.
    """
    return MSG_FLAG_RE.sub(" MESSAGE ", strip_heredocs(text))


def strip_heredocs(text):
    """strip_data's heredoc half alone: each masked heredoc's body blanked,
    its opening line kept as code."""
    masked = {m.span(): m for _, m in _masked_heredocs(text)}
    return HEREDOC_RE.sub(
        lambda m: " HEREDOC " + _heredoc_opener_rest(m)
        if m.span() in masked else m.group(0), text)


def _covers_home(d):
    """Is d the home directory, one of its ancestors, or /? A directory like
    that is no safe root: everything the user owns would sit under it.

    Read as written and, unless --no-disk, through every symlink too: a cwd
    that is a link to / or to the home covers it as surely as the path. A
    disk error counts as covering.
    """
    def covers(d, h):
        return not d.strip("/") or (bool(h) and (d == h or _under(h, d)))
    d, h = posixpath.normpath(d), _home()
    if covers(d, h):
        return True
    if NO_DISK:
        return False
    try:
        return covers(os.path.realpath(d), h and os.path.realpath(h))
    except (OSError, ValueError):
        return True


def safe_roots(cwd, home=None):
    """-> the directories anything strictly below which clears.

    cwd is the session's own -- the hook payload's -- and never a cd target;
    see classify_path. It and TMPDIR are roots only when they do not hold
    the home: a session started in ~ would otherwise clear ~/Documents.
    """
    roots = ["/tmp", "/var/tmp"]
    for var in ("TMPDIR",):
        v = os.environ.get(var)
        if v and posixpath.isabs(v) and not _covers_home(v):
            roots.append(posixpath.normpath(v))
    if cwd and posixpath.isabs(cwd) and not _covers_home(cwd):
        roots.append(posixpath.normpath(cwd))
    roots.extend(home_roots(home))
    return roots


def home_roots(home):
    """~/temp and ~/.cache: scratch and regenerable by convention on this
    machine, and only for a local analysis -- a far end's home is not ours."""
    return [posixpath.join(home, "temp"), posixpath.join(home, ".cache")] if home else []


def _under(p, d):
    """Is p strictly below d? Both absolute and free of `.` and `..`."""
    return p.startswith(d.rstrip("/") + "/")


def _stays_under(p, root, home):
    """Does p, every symlink that exists resolved, still lie under root?

    p is the path as written, not its normpath: the kernel follows a link
    before the `..` after it, so `~/temp/link/../x` is wherever link leads,
    one up. realpath resolves in that same order, and keeps a missing tail as
    written, so a path that does not exist yet is judged by the part of it
    that does. A root that resolves to / or to a directory holding the home
    is no scratch directory, and nothing clears under it.
    """
    # Unlike the git rule, which --no-disk turns off, this keeps the lexical
    # rule the /tmp root has: the corpus replays an anonymised fixture where
    # nothing exists, and there that is the comparison worth making.
    if NO_DISK:
        return True
    try:
        real_root = os.path.realpath(root)
        real_home = os.path.realpath(home)
        if not real_root.strip("/") or real_home == real_root or _under(real_home, real_root):
            return False
        return _under(os.path.realpath(p), real_root)
    except (OSError, ValueError):
        return False


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


def _git_marker(d):
    """Is d/.git a real repository marker: a directory holding HEAD, or a file
    whose first line is a `gitdir:` pointer (a linked worktree or submodule)?

    A .git that is a symlink, to a directory or to a file, is not one. The
    file is opened non-blocking and checked once open, as a sourced file is:
    a FIFO swapped in after the stat would hang the hook.
    """
    g = os.path.join(d, ".git")
    try:
        if os.path.islink(g):
            return False
        if os.path.isdir(g):
            return os.path.isfile(os.path.join(g, "HEAD"))
        if os.path.isfile(g):
            fd = os.open(g, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    return False
                return fh.read(8) == b"gitdir: "
    except (OSError, ValueError):
        return False
    return False


def _tree_of(p, home):
    """-> the work tree p sits strictly inside, read off p's own spelling.

    Walks up from p's parent to the first directory with a .git entry, and
    stops before home, so a home that is itself a repository clears nothing.
    None when p holds a .git of its own (it is a tree's top, nested or not),
    when it lies in the .git of the tree found, or when that .git is no
    repository marker. The walk does not go past a .git that is no marker,
    so a stray or empty .git inside a real tree makes all below it ask.
    """
    if not _under(p, home) or os.path.lexists(os.path.join(p, ".git")):
        return None
    d = posixpath.dirname(p)
    while _under(d, home):
        if os.path.lexists(os.path.join(d, ".git")):
            first = p[len(d) + 1:].split("/", 1)[0]
            if not _git_marker(d) or first.lower() == ".git":
                return None
            return d
        d = posixpath.dirname(d)
    return None


def _hidden_top(tree, home):
    """Is tree under a hidden top-level directory of home -- ~/.dotfiles,
    ~/.password-store, ~/.local, ~/.config? tree is strictly under home."""
    return tree[len(home.rstrip("/")) + 1:].startswith(".")


def inside_work_tree(p, home):
    """-> the git work tree p sits strictly inside, or None.

    p is the path as written. Both its spelling and its realpath -- every
    symlink that exists resolved, in the kernel's order -- must sit strictly
    inside a tree below the home, and the real one inside the written one's
    real directory. So a link out of the tree, a link into a .git or onto a
    tree's top, and a `..` after a link do not ride the parent's verdict.

    No tree under a hidden top-level directory of the home counts, by either
    reading: the user's decision. Those hold dotfiles, a password store, and
    tools' own clones, where "it is in git" says least about what a delete
    loses. ~/.cache keeps its home-root rule. Local only, and off under
    --no-disk, since it reads the disk.
    """
    if NO_DISK or not home:
        return None
    try:
        tree = _tree_of(posixpath.normpath(p), home)
        if not tree or _hidden_top(tree, home):
            return None
        real_home = os.path.realpath(home)
        real = _tree_of(os.path.realpath(p), real_home)
        real_tree = os.path.realpath(tree)
        if not real or not (real == real_tree or _under(real, real_tree)):
            return None
        if _hidden_top(real, real_home):
            return None
        return tree
    except (OSError, ValueError):
        return None


# A delete inside a work tree may still hold another one: a registered linked
# worktree, a submodule, or a clone nobody registered. How far below the
# delete the guard looks for the last kind, breadth first: levels below it,
# and entries listed in all.
NESTED_WALK_DEPTH = 3
NESTED_WALK_ENTRIES = 1000
# The most of a .gitmodules or a git pointer file read; a larger one asks.
MAX_GIT_FILE_BYTES = 64 * 1024


def _read_git_file(path):
    """-> the text of a small regular file, or None when there is none.

    Raises OSError for anything else -- a FIFO, a directory, a file over
    MAX_GIT_FILE_BYTES -- which the caller turns into an ask. Opened
    non-blocking and checked once open, as _git_marker does.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError(f"{path} is not a regular file")
        data = fh.read(MAX_GIT_FILE_BYTES + 1)
    if len(data) > MAX_GIT_FILE_BYTES:
        raise OSError(f"{path} is too large to read")
    return data.decode("utf-8", errors="replace")


def _first_line(text):
    """git's reading of a pointer file: the trailing newline trimmed."""
    return text.split("\n", 1)[0].rstrip("\r")


def _common_dir(tree):
    """-> the git directory that registers tree's linked worktrees.

    tree's .git is a marker (see _git_marker). A directory is its own common
    dir. A `gitdir:` file, relative to tree when not absolute, names either
    a linked worktree's admin directory, whose `commondir` file (relative to
    that directory) leads to the main repository's .git, or a submodule's
    repository under modules/, which has no commondir and is its own.
    Raises OSError when a pointer leads nowhere.
    """
    g = os.path.join(tree, ".git")
    if os.path.isdir(g) and not os.path.islink(g):
        return g
    text = _read_git_file(g)
    if text is None or not text.startswith("gitdir: "):
        raise OSError(f"{g} is no gitdir pointer")
    gitdir = posixpath.join(tree, _first_line(text)[len("gitdir: "):])
    if not os.path.isdir(gitdir):
        raise OSError(f"{g} points at no git directory")
    common = _read_git_file(os.path.join(gitdir, "commondir"))
    if common is None:
        return gitdir
    common = posixpath.join(gitdir, _first_line(common))
    if not os.path.isdir(common):
        raise OSError(f"{gitdir}/commondir points at no git directory")
    return common


def _registered_trees(common):
    """-> the top of every work tree common knows besides its submodules: each
    linked worktree, from worktrees/<id>/gitdir, which holds the path of that
    worktree's .git file (relative to worktrees/<id>/ when not absolute),
    and the main worktree when common is a .git directory."""
    tops = []
    if posixpath.basename(posixpath.normpath(common)) == ".git":
        tops.append(posixpath.dirname(posixpath.normpath(common)))
    wdir = os.path.join(common, "worktrees")
    try:
        ids = os.listdir(wdir)
    except (FileNotFoundError, NotADirectoryError):
        return tops
    if len(ids) > NESTED_WALK_ENTRIES:
        raise OSError(f"{wdir} registers too many worktrees to read")
    for i in ids:
        admin = os.path.join(wdir, i)
        text = _read_git_file(os.path.join(admin, "gitdir"))
        if text is None:
            continue                  # git prunes it; it names no tree
        dotgit = posixpath.normpath(posixpath.join(admin, _first_line(text).rstrip()))
        tops.append(posixpath.dirname(dotgit))
    return tops


GITMODULES_PATH_RE = re.compile(r"^[ \t]*path[ \t]*=[ \t]*(.*?)[ \t]*$", re.M | re.I)


def _submodule_tops(tree):
    """-> the path of every submodule tree's .gitmodules names, under tree.

    A quoted value ends at its closing quote; an unquoted one at a comment.
    A misread costs only this check: the walk below is the backstop.
    """
    text = _read_git_file(os.path.join(tree, ".gitmodules"))
    out = []
    for m in GITMODULES_PATH_RE.finditer(text or ""):
        v = m.group(1)
        if v.startswith('"'):
            v = v[1:].split('"', 1)[0]
        else:
            v = re.split(r"[ \t]*[#;]", v, maxsplit=1)[0]
        if v:
            out.append(posixpath.normpath(posixpath.join(tree, v)))
    return out


def _git_entry_below(top):
    """-> the first .git entry below top, or None.

    Breadth first and never through a symlink, NESTED_WALK_DEPTH levels down
    and at most NESTED_WALK_ENTRIES entries listed. Reaching that bound ends
    the walk with None, so a large build directory (target/, node_modules/)
    keeps its allow. The residual is accepted: a clone nobody registered,
    deeper than the bound, or past the first thousand entries, is not seen.
    """
    level, seen = [top], 0
    for _ in range(NESTED_WALK_DEPTH):
        below = []
        for d in level:
            try:
                it = os.scandir(d)
            except (FileNotFoundError, NotADirectoryError):
                continue              # nothing there, or a file: nothing below
            with it:
                for e in it:
                    seen += 1
                    if e.name.lower() == ".git":
                        return e.path
                    if seen >= NESTED_WALK_ENTRIES:
                        return None
                    if e.is_dir(follow_symlinks=False):
                        below.append(e.path)
        level = below
    return None


def nested_work_tree(p, home):
    """-> why deleting p would take another work tree with it, or None.

    The user's decision: a delete that contains a nested work tree asks. p
    is strictly inside a tree (see inside_work_tree), and is judged by its
    realpath, as rm descends it. It asks when it is, or holds, a worktree
    the enclosing tree's repository registers or a submodule its
    .gitmodules names -- exact, at any depth -- or when a shallow walk
    below it meets a .git entry. A disk error asks.
    """
    norm = posixpath.normpath(p)
    if NO_DISK:
        return "the guard reads no disk under --no-disk"
    try:
        real = os.path.realpath(p)
        tree = _tree_of(real, os.path.realpath(home))
        if not tree:
            return f"{norm} is in no work tree the guard can read"
        for top in _registered_trees(_common_dir(tree)) + _submodule_tops(tree):
            rt = os.path.realpath(top)
            if real == rt or _under(rt, real) or norm == top or _under(top, norm):
                return (f"{top} is a work tree the repository at {tree} knows, "
                        f"and deleting {norm} takes it")
        found = _git_entry_below(real)
        if found:
            return f"{found} marks a work tree below {norm}, which this takes"
    except (OSError, ValueError) as e:
        return f"the guard could not read what lies below {norm} ({e})"
    return None


def classify_path(p, cwd, home=None, through_glob=False, base=None):
    """Classify a fully-literal path. -> (decision, reason)

    cwd is where the shell stands when this runs: the session's own, or a cd
    target on the line. base is the session's own, which the safe roots are
    built from, so a cd moves where a relative path lands but never makes its
    own target a root. None takes cwd, for a caller with one cwd and no cd.
    home is the local $HOME, or None for a far end; see analyse.
    through_glob is True for a glob's literal prefix when a `/` follows the
    wildcard: the shell may then walk through a link no one can see yet, so
    neither home root nor work tree may clear it.
    """
    if not posixpath.isabs(p):
        # A literal relative path is resolved against the cwd it runs in and
        # then judged as the absolute path it becomes.
        if ".." in p.split("/"):
            return "ask", "relative path escapes upward via '..'"
        if cwd == UNKNOWN_CWD:
            return "ask", ("a `cd`, `pushd` or `popd`, or a wrapper's "
                           "directory option such as `env -C`, in this command "
                           "goes somewhere the guard cannot resolve, so where "
                           "this lands is unknown")
        # An operand that IS cwd -- `.`, `./`, or the `.` a `./*` glob strips
        # down to -- takes the whole tree you are standing in, which is a
        # different thing to agree to and gets its own prompt.
        whole_cwd = posixpath.normpath(p) == "."
        # `.` IS cwd, and `./x` is one level under it. Harmless in a project
        # directory, catastrophic at / or $HOME. cwd is in the hook payload, so
        # resolve and check those cases.
        if cwd and posixpath.isabs(cwd):
            resolved = posixpath.normpath(posixpath.join(cwd, p))
            env_home = os.environ.get("HOME", "")
            depth = len([x for x in resolved.split("/") if x])
            if resolved == "/" or depth <= 1:
                return "deny", f"relative path resolves to {resolved}"
            if env_home and resolved == posixpath.normpath(env_home):
                return "deny", "relative path resolves to the home directory"
            if whole_cwd:
                return "ask", ("operand is the working directory itself "
                               f"({resolved}), so everything under it goes")
            # Where the operand LANDS is judged, against the roots an
            # absolute path faces. "A named subdirectory is bounded wherever
            # cwd happens to be" was true of its depth and not of what it
            # was: `cd && rm -rf ./Documents` cleared as a relative path.
            d, r = classify_path(resolved, cwd, home, through_glob,
                                 cwd if base is None else base)
            return d, f"lands at {resolved}; {r}"
        # No cwd to resolve against -- the far side of an ssh, or a container,
        # where the working directory is whatever the login or the image
        # starts in, usually a $HOME no rule here can see. `ssh h 'rm -rf
        # ./Documents'` is `cd && rm -rf ./Documents` there.
        if whole_cwd:
            return "ask", "operand is the working directory, unknown on the far end"
        return "ask", ("a relative path on the far end lands in whatever directory "
                       "it starts in, usually its $HOME, which nothing here can place")

    norm = posixpath.normpath(p)
    if norm == "/":
        return "deny", "operand is the filesystem root"
    # This machine's $HOME, wherever the operand came from: `$HOME` and `~`
    # now resolve to it, and the bare-variable deny no longer sees them.
    h = _home()
    if h and norm == h:
        return "deny", "operand is the home directory"
    parts = [x for x in norm.split("/") if x]
    homes = home_roots(home)
    for root in safe_roots(cwd if base is None else base, home):
        rp = [x for x in posixpath.normpath(root).split("/") if x]
        if parts[:len(rp)] == rp and len(parts) >= len(rp) + 1:
            if root in homes and through_glob:
                return "ask", (f"a wildcard mid-path under {root} may pass "
                               "through a symlink out of it")
            if root in homes and not _stays_under(p, root, home):
                return "ask", f"{norm} leaves {root} through a symlink"
            return "allow", f"literal path under safe root {root}"
    tree = inside_work_tree(p, home)
    if tree and through_glob:
        return "ask", (f"a wildcard mid-path inside the git work tree {tree} "
                       "may pass through a symlink out of it")
    held = tree and nested_work_tree(p, home)
    if held:
        return "ask", held
    if tree:
        return "allow", f"inside the git work tree {tree}"
    return "ask", f"absolute path outside safe roots: {norm}"


def worst_of(results):
    """-> the harshest (decision, reason) in results, ties going to the first."""
    best = None
    for d, r in results:
        if best is None or RANK[d] > RANK[best[0]]:
            best = (d, r)
    return best or ("ask", "nothing to classify")


def classify_operand(op, cwd, bare_var_ok=True, assigned=None, loops=None,
                     may_clear=False, loops_may_clear=True, opaque_names=frozenset(),
                     line_literals=None, reread=False, glued=False, home=None,
                     base=None, condemn_cwds=(), pwd_here=False,
                     rebuilt_names=frozenset()):
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

    loops_may_clear is False inside a string another shell re-reads; see
    analyse's `trusted`. opaque_names, rebuilt_names, line_literals, reread,
    glued, home and pwd_here are carried through to classify_one.

    cwd is one directory or candidate_cwds' list, whose first is the
    session's own. That one, and never a cd target, is what the safe roots
    are built from; see classify_path. base names it when the list no longer
    starts with it -- a command that runs only after a cd succeeded is never
    in the session's cwd; see cd_must_have_run.

    condemn_cwds are readings the command cannot run in but that the
    line-wide reading counted: the session's cwd after such a cd, and a cd
    that comes after the command; see _analyse's cwds_at. Each is still
    judged, and only a deny it finds stands, so no deny is lost.
    """
    cwds = [cwd] if isinstance(cwd, str) else list(cwd) or [""]
    base = cwds[0] if base is None else base
    products = expand_braces(op)
    if len(products) >= MAX_BRACE_PRODUCTS:
        return "ask", "brace expansion produces too many paths to check"
    results = []
    for p in products:
        for c in cwds + [c for c in condemn_cwds if c not in cwds]:
            d, r = classify_one(p, c, bare_var_ok, assigned, loops, may_clear,
                                loops_may_clear, opaque_names=opaque_names,
                                rebuilt_names=rebuilt_names,
                                line_literals=line_literals, reread=reread,
                                glued=glued, home=home, base=base,
                                pwd_here=pwd_here)
            if c not in cwds and d != "deny":
                continue
            if p != op:
                r = f"brace expansion produces {p}; {r}"
            results.append((d, r))
    return worst_of(results)


def classify_one(op, cwd, bare_var_ok=True, assigned=None, loops=None,
                 may_clear=False, loops_may_clear=True, opaque_names=frozenset(),
                 line_literals=None, reread=False, glued=False, home=None,
                 base=None, pwd_here=False, rebuilt_names=frozenset()):
    """-> (decision, reason). Anything not provably safe returns ask/deny.

    bare_var_ok encodes an rm-specific fact: `rm -rf $X` with X unset or empty
    is an *error*, not a catastrophe, so a bare expansion is safe there. It is
    not safe for commands that treat "no path" as "use the current directory" --
    `find $X -delete` with X unset becomes `find -delete`, which is `rm -rf .`.

    opaque_names are the names this line assigns to a value the guard cannot
    read (`d=$(cat f)`, `d=$1`). Such a value keeps a loop's words from
    clearing, since the body may have replaced them. A plain assignment of
    one does not touch the free passes: "empty only errors" for a bare $d
    and "non-empty" for ${d:?} hold whoever chose the value, and `D=$S/sub;
    rm -rf "${D:?}"` is the form this guard's advice asks for.
    rebuilt_names are the opaque names the line sets some other way, which
    unread_sets finds: an append, an element, an array, `${W:=x}`, a nameref,
    printf -v, mapfile, getopts. The text rewrites them in plain sight --
    `W=/tmp/x; W+=/../../usr` -- so neither free pass covers them.
    line_literals ({name: values}) are the literals the line assigns names
    that proven_bindings could not prove, as in `echo; W=/; rm -rf "$W"`,
    including a name it assigns other values too. Each value is judged, and
    may condemn; it never clears, because the assignment may not have run --
    the free pass is then all that is left.

    may_clear is analyse's finding that nothing before this operand's command
    could have rebound a proven binding -- see _rebinding_possible. False is
    the safe default: a caller that does not know gets condemn-only.

    loops_may_clear is False inside a string another shell re-reads, where the
    outer shell may have expanded a loop variable first: a loop's words may
    then condemn but not clear. See analyse's `trusted`.

    reread is True for text another reader runs -- a `-c` script, a heredoc
    fed to a shell, a substitution's body, an ssh remote string. A variable
    there may take its value from outside that text: the outer shell expands
    a double-quoted script first, and a substitution or a heredoc-fed shell
    sees the line's own variables and exports. So neither free pass holds,
    and `D=/; echo $(rm -rf $D)` asks: an operand with an expansion in it
    asks unless something resolves it to a deny.

    glued is True for a line where an expansion meets a quote and then a word
    character -- see QUOTE_GLUE_RE. The tokens have lost the quotes, so the
    name read here may not be the one the shell expands, and neither free
    pass holds either.

    home is the local $HOME, or None on a far end; see analyse. A `~` there
    reads as the HOME binding analyse adds, when it adds one. base is the
    session's cwd, which the safe roots come from; see classify_path.

    pwd_here is analyse's finding that $PWD here is the directory the shell
    stands in: the line does not write PWD, and nothing before this command
    can have; see _analyse. `"${PWD:?}/x"` is then `./x`, judged against
    this cwd exactly as `./x` is. A bare `"${PWD:?}"` keeps its deny.
    """
    if XARGS_INPUT in op:
        return "ask", ("xargs supplies this operand from its input, which is "
                       "not in the command")
    if "`" in op or "$(" in op:
        return "ask", "command substitution cannot be statically resolved"
    pm = PWD_PREFIX_RE.match(op) if pwd_here else None
    if pm:
        rel = "." + pm.group(1)
        d, r = classify_one(rel, cwd, bare_var_ok, assigned, loops, may_clear,
                            loops_may_clear, opaque_names=opaque_names,
                            rebuilt_names=rebuilt_names,
                            line_literals=line_literals, reread=reread,
                            glued=glued, home=home, base=base)
        return d, f"$PWD is the working directory here, so this reads as {rel}; {r}"

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
    def condemned_by_line(names, empty_too=False):
        """The worst non-allow verdict among op's readings under each literal
        value the line assigns one of names, or None. An empty value is
        skipped: "" makes the bare form error out and the ${W:?} form abort.
        empty_too keeps it, for an operand with a suffix, where "" is what
        turns "$W"/* into /* -- unless a ${W:?} in it aborts first.
        One name at a time, the rest left to the recursive call."""
        for n in names:
            if n not in (line_literals or {}):
                continue
            rest = {k: v for k, v in line_literals.items() if k != n}
            aborts = any(":?" in mm.group(0) for mm in SUBST_NAME_RE.finditer(op)
                         if (mm.group(1) or mm.group(2)) == n)
            out = []
            for val in line_literals[n]:
                if not val and (not empty_too or aborts):
                    continue
                sub = SUBST_NAME_RE.sub(
                    lambda mm, v=val: v if (mm.group(1) or mm.group(2)) == n
                    else mm.group(0), op)
                if sub == op:
                    continue
                d, r = classify_one(sub, cwd, bare_var_ok, assigned,
                                    may_clear=may_clear, opaque_names=opaque_names,
                                    rebuilt_names=rebuilt_names,
                                    line_literals=rest, reread=reread, glued=glued,
                                    home=home, base=base)
                out.append((d, f"{n}={val} on this line, so this may read as "
                               f"{sub}; {r}"))
            d, r = worst_of(out) if out else ("allow", "")
            if d != "allow":
                return d, r
        return None

    # A local `~` is $HOME, and $HOME is a binding when analyse could add one,
    # so it is rewritten into the form the block below resolves and gates.
    # Unresolved, it goes back as written, for the tilde rules further down.
    tilde = op
    if (home and assigned and "HOME" in assigned
            and (op == "~" or op.startswith("~/"))):
        op = "${HOME}" + op[1:]

    if assigned:
        zsh_read = []

        def bound(m):
            val = assigned.get(m.group(1) or m.group(2))
            # ${W:?} never expands an empty value -- it aborts, and the delete
            # does not run -- so substituting "" there would judge a path the
            # shell never produces. $W and ${W} do expand to "", and that ""
            # is what turns "$W"/* into /*.
            if val is None or (val == "" and ":?" in m.group(0)):
                return m.group(0)
            if _zsh_suffixed(m):
                zsh_read.append(m.group(2))
            return val
        resolved = SUBST_NAME_RE.sub(bound, op)
        if resolved != op and not EXPANSION_RE.search(resolved):
            d, r = classify_one(resolved, cwd, bare_var_ok, home=home, base=base)
            used = [n for n in dict.fromkeys(
                m.group(1) or m.group(2) for m in SUBST_NAME_RE.finditer(op))
                if n in assigned]
            # A glued operand names another variable than the one read here:
            # `Wusr=/tmp/x; W=/; rm -rf "$W"usr` is rm -rf /usr, not /tmp/x.
            # A zsh-suffixed one is not W's value at all; see _zsh_suffixed.
            # Both may still condemn.
            #
            # An ask condemns too, as a literal the line assigns does, once
            # every value is non-empty: falling through would hand the line's
            # own `W=/` to the ${W:?} free pass, whose premise is that nothing
            # on the line chose W -- `W=/; printf x; rm -rf "${W:?}"/x`
            # cleared as guarded, and is rm -rf /x.
            nonempty = all(assigned[n] for n in used)
            if d == "deny" or (d == "ask" and nonempty) or (
                    may_clear and not glued and not zsh_read and nonempty):
                shown = ", ".join("%s=%s" % (n, assigned[n]) for n in used)
                return d, f"{shown} in this command, so this reads as {resolved}; {r}"
    op = tilde

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
                                    may_clear=may_clear, opaque_names=opaque_names,
                                    rebuilt_names=rebuilt_names,
                                    line_literals=line_literals, reread=reread,
                                    glued=glued, home=home, base=base)
                out.append((d, f"the loop sets ${name} to {val}; {r}"))
            d, r = worst_of(out)
            # The same misread as the binding block above: the loop's name is
            # not the one a glued operand expands.
            if d == "allow" and glued:
                return "ask", GLUED_REASON
            # ...nor is it the loop's word that zsh expands after it.
            if d == "allow" and any(
                    _zsh_suffixed(m) and m.group(2) == name
                    for m in SUBST_NAME_RE.finditer(op)):
                return "ask", (f"{r} -- but zsh reads ${name} with what follows "
                               "it, as a modifier (:h) or a subscript ([1]), "
                               "so the loop's word is not what this expands to")
            # The body may assign the name too: `for d in /tmp/y; do d=/;
            # rm -rf "$d"/*; done` is rm -rf //*. Such a value may condemn,
            # and one the guard cannot read keeps the loop's words from
            # clearing.
            if d == "allow":
                worse = condemned_by_line([name], empty_too=not BARE_VAR_RE.match(op))
                if worse:
                    return worse
                if name in (opaque_names or ()) or ANY_NAME in (opaque_names or ()):
                    return "ask", (f"{r} -- but ${name} is also assigned on this "
                                   "line to a value the guard cannot read")
            if d == "allow" and isinstance(values, OutsideLoop):
                return "ask", (f"{r} -- but this runs outside the loop that "
                               f"sets ${name}, which may never have run here")
            if d == "allow" and isinstance(values, SplitWords):
                return "ask", (f"{r} -- but an unquoted ${name} may split the "
                               "loop's words into other paths, on whitespace in "
                               "a word or on an IFS the line writes")
            # Inside a string another shell re-reads, the outer shell may have
            # expanded $name before the inner one ever ran the loop, so the
            # words above prove nothing. They may still condemn.
            if d == "allow" and not loops_may_clear:
                return "ask", (f"{r} -- but another shell re-reads this text, "
                               f"and the outer one may already have expanded "
                               f"${name}")
            return d, r

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
            # A literal still live here condemns before an unread set of the
            # same name asks: `W=/; : ${W:=/tmp/x}; rm -rf "$W"` is rm -rf /.
            worse = condemned_by_line([name])
            if worse:
                return worse
            if name in rebuilt_names or ANY_NAME in rebuilt_names:
                return "ask", (f"${name} is rebuilt on this line in a way the "
                               "guard does not follow, so an empty one is not "
                               "the only way it goes wrong")
            if not bare_var_ok:
                return "ask", (
                    f"bare ${name} -- if it is unset this command falls back to "
                    f"the current directory. Use \"${{{name}:?}}\" instead."
                )
            if reread:
                return "ask", (
                    f"${name} in text another reader runs may take its value "
                    "from outside it -- the outer shell or an export -- so an "
                    "empty one is not the only way it goes wrong")
            if glued:
                return "ask", GLUED_REASON
            # Bare expansion, no suffix: empty -> `rm -rf ""` (errors),
            # unset+unquoted -> no operands (errors). Nothing to promote to /.
            return "allow", f"bare ${name} with no suffix"
        if all(GUARDED_RE.match(e) for e in exps):
            guarded = list(dict.fromkeys(EXP_NAME_RE.match(e).group(1) for e in exps))
            worse = condemned_by_line(guarded)
            if worse:
                return worse
            # The free pass is "non-empty, so a path under it": no help for a
            # name that holds a critical directory. `"${HOME:?}"` is the
            # home directory exactly as bare $HOME is, and `"${PWD:?}"` the
            # working one. Reached when no HOME binding resolved it first --
            # the line writes HOME, or `git add .` drops the binding -- and
            # for PWD when pwd_here does not hold.
            crit = next((n for n in guarded if n in DANGEROUS_VARS), None)
            if crit and GUARDED_RE.match(op):
                return "deny", f"${{{crit}:?}} expands to a critical directory"
            if crit:
                return "ask", (f"${{{crit}:?}} proves only that it is not empty, "
                               f"and ${crit} holds a critical directory")
            held = next((n for n in guarded
                         if n in rebuilt_names or ANY_NAME in rebuilt_names), None)
            if held:
                return "ask", (f"${held} is rebuilt on this line in a way the "
                               f"guard does not follow, and ${{{held}:?}} proves "
                               "only that it is not empty")
            if reread:
                return "ask", (f"${{{guarded[0]}:?}} proves only that it is not "
                               "empty, and in text another reader runs its value "
                               "may come from outside it")
            if glued:
                return "ask", GLUED_REASON
            return "allow", "all expansions are ${VAR:?}-guarded"
        flagged = next((e for e in exps if ZSH_FLAG_EXPANSION_RE.match(e)), None)
        if flagged:
            return "ask", (f"zsh expands {flagged} as a glob, a word list or a "
                           "count rather than as one path, so no value of it "
                           "is safe to assume")
        # A literal the line assigns may condemn here too, and an empty one
        # most of all: `echo; W=; rm -rf "$W"/*` runs as `rm -rf /*`. Only a
        # deny is taken, so an ask keeps the advice below.
        worse = condemned_by_line(
            dict.fromkeys(m.group(1) for m in
                          (EXP_NAME_RE.match(e) for e in exps) if m),
            empty_too=True)
        if worse and worse[0] == "deny":
            return worse
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
        return classify_path(prefix, cwd, home, through_glob="/" in op[idx:],
                             base=base)

    return classify_path(op, cwd, home, base=base)


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


# Inside backticks bash drops the backslash from `\\`, `` \` `` and `\$` before
# it reads the body as a command, which is how one backtick span nests in
# another: the body of `` `echo \`pwd\`` `` runs as `` echo `pwd` ``.
BACKTICK_ESCAPE_RE = re.compile(r"\\([\\`$])")


def _close_backtick(s, start):
    """Index of the backtick closing the one at s[start]. Backticks do not nest
    unescaped; see BACKTICK_ESCAPE_RE."""
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
    for ph, m in _masked_heredocs(cmd):
        parts.append(cmd[last:m.start()])
        parts.append(ph)
        parts.append(_heredoc_opener_rest(m))
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
        if quote is None and c == "#" and (not out or _is_boundary(out[-1])):
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


def command_name(t):
    """The command a word names: its basename, or the name in zsh's `=name`.

    zsh's EQUALS expansion replaces a word `=rm` with the full path of rm, in
    any position, so `=rm -rf /` is rm and `find / -exec =rm -rf {} +` runs it.
    Read as a basename, `=rm` was a word naming nothing.
    """
    if t[:1] == "=" and BARE_NAME_RE.match(t[1:]):
        return t[1:]
    return os.path.basename(t)


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
    j, base, _, _ = command_word_detail(argv)
    return j, base


def command_word_detail(argv):
    """-> (index, basename, moved, known): command_word, and what a wrapper's
    options in front of the command say about it.

    moved: a wrapper option runs the command in another directory
    (WRAPPER_CHDIR). known: False when the walk stopped on a wrapper's option
    or positional word it cannot read, so the index is that word's and the
    real command is somewhere after it, unknown.
    """
    # The wrapper whose options are still open. A redirection does not close
    # them: `env >x -i CMD` hands env `-i CMD`. An assignment closes env's --
    # `env A=1 -u B` runs a command named -u, which fails -- but not sudo's,
    # which takes VAR=value among its options: `sudo A=1 -u root CMD`.
    # pending counts the wrapper's positional words still to come.
    j, moved, known, open_opts, pending = 0, False, True, None, 0
    while j < len(argv):
        t = argv[j]
        if open_opts and t.startswith("-"):
            nj, m, known, still = _past_wrapper_opts(argv, j, open_opts)
            advanced, j, moved = nj != j, nj, moved or m
            if not known:
                break
            if advanced:
                open_opts = still and open_opts
                continue
            open_opts = None    # a lone `-`: not an option, so the command
        if pending:
            # A redirection before the word is the shell's: `timeout >x 5
            # CMD`. A word FOLLOWED by one is either: `5 >x` hands timeout
            # the 5, `5>x` redirects fd 5, and the tokens are the same.
            fd_like = ((t.isdigit() or FD_BRACE_RE.match(t)) and j + 1 < len(argv)
                       and REDIRECT_TOKEN_RE.match(argv[j + 1]))
            if fd_like or has_subst(t) or EXPANSION_RE.search(t):
                known = False
                break
            if REDIRECT_TOKEN_RE.match(t) and j + 1 < len(argv):
                j += 2
                continue
            pending, open_opts, j = pending - 1, None, j + 1
            continue
        if ENV_ASSIGN_RE.match(t) or t in KEYWORDS:
            if not (open_opts == "sudo" and ENV_ASSIGN_RE.match(t)):
                open_opts = None
            j += 1
            continue
        name = command_name(t)
        if name in WRAPPERS:
            j, m, known, still = _past_wrapper_opts(argv, j + 1, name)
            moved, open_opts = moved or m, still and name
            pending = WRAPPER_POSITIONALS.get(name, 0)
            if not known:
                break
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
        return j, "", moved, known
    return j, command_name(argv[j]), moved, known


def _past_wrapper_opts(argv, j, name):
    """-> (index past wrapper `name`'s options from argv[j], moved, known,
    still_open).

    A bundle takes a value from the rest of its word or else the next word:
    `-u root`, `-uroot`, `-iu FOO`. `--` ends the options, and still_open is
    then False. See command_word_detail for moved and known.
    """
    flags, values, long_values, long_flags = WRAPPER_OPTS.get(
        name, ("", "", set(), set()))
    chdir_letters, chdir_long = WRAPPER_CHDIR.get(name, ("", set()))
    moved = False
    while j < len(argv):
        t = argv[j]
        if t == "--":
            return j + 1, moved, True, False
        if t == "-" and name == "env":     # env's old spelling of -i
            j += 1
            continue
        if not t.startswith("-") or t == "-":
            return j, moved, True, True
        if name == "nice" and t[1:].lstrip("+-").isdigit():  # `nice -5`, `--5`
            j += 1
            continue
        if t.startswith("--"):
            opt = t.split("=", 1)[0]
            if opt in long_values:
                moved = moved or opt in chdir_long
                j += 1 if "=" in t else 2
            elif opt in long_flags:
                j += 1
            else:
                return j, moved, False, True
            continue
        for k in range(1, len(t)):
            c = t[k]
            if c in values:
                moved = moved or c in chdir_letters
                j += 1 if k + 1 < len(t) else 2
                break
            if c not in flags:
                return j, moved, False, True
        else:
            j += 1
    return min(j, len(argv)), moved, True, True


def parse_xargs(argv, j):
    """xargs at argv[j] -> (index of the command it runs, its -I string).

    The index is len(argv) when xargs names no command, and so runs echo. The
    -I string is None when there is none. -> None when an option cannot be
    read, which the caller turns into an ask. Options end at the first word
    that is not one, as GNU xargs's `+` getopt string says: `xargs rm -i`
    hands -i to rm. A redirection among them is the shell's, gone before
    xargs runs, so it is stepped over as command_word steps over one --
    before a detached value too: `-n 2>/dev/null 1` is -n 1.

    GNU xargs undoes an -I that a later -L, -l or --max-lines follows, and
    one that a later -n or --max-args follows unless its count is 1, and
    appends the input again: `xargs -I{} -L1 rm -rf ./build` runs `rm -rf
    ./build` and the input. A later -I wins over all of them. A count that
    is not a literal number leaves that unknown, so it is unreadable then.
    """
    i, replace = j + 1, None
    while True:
        i = _past_redirections(argv, i, len(argv))
        if i >= len(argv):
            break
        t = argv[i]
        if t == "--":
            i += 1
            break
        if not t.startswith("-") or t == "-":
            break
        if t.startswith("--"):
            opt, given, val = t.partition("=")
            if opt in XARGS_LONG_FLAGS and not given:
                i += 1
                continue
            if opt not in XARGS_LONG_VALUE and opt not in XARGS_LONG_OPTIONAL:
                return None
            if opt in XARGS_LONG_VALUE and not given:
                i = _past_redirections(argv, i + 1, len(argv))
                if i >= len(argv):
                    return None
                given, val = "=", argv[i]
        else:
            opt, given, val = None, "", ""
            for k in range(1, len(t)):
                c = t[k]
                if c in XARGS_FLAGS:
                    continue
                if c not in XARGS_VALUE and c not in XARGS_OPTIONAL:
                    return None
                opt, val = XARGS_LONG_NAME.get(c, "-" + c), t[k + 1:]
                if c in XARGS_VALUE and not val:
                    i = _past_redirections(argv, i + 1, len(argv))
                    if i >= len(argv):
                        return None
                    val = argv[i]
                given = "=" if c in XARGS_VALUE or val else ""
                break
        i += 1
        if opt in ("-I", "--replace"):
            replace = val if given else "{}"
        elif opt in ("-L", "--max-lines"):
            replace = None
        elif opt == "--max-args" and replace is not None:
            if not XARGS_COUNT_RE.match(val):
                return None
            if int(val) != 1:
                replace = None
    # An empty -I string would stand in every word at once.
    if replace == "":
        return None
    return min(i, len(argv)), replace


def effective_command(argv):
    """-> (index, basename, moved, known, runs): command_word_detail's answer,
    carried on past each xargs to the command that xargs runs.

    runs lists each xargs crossed as (start, replace): where the command it
    runs begins -- len(argv) when it names none and so runs echo -- and its -I
    string or None. known is False with the index ON the xargs when its options
    cannot be read. An index of len(argv) behind an xargs that did name
    something means it named only wrappers -- `xargs sudo`, `xargs timeout 5`
    -- and the command they run is xargs's input. moved covers the wrappers on
    both sides: `xargs env -C / rm` runs rm in /.
    """
    off, moved, runs = 0, False, []
    while True:
        j, base, m, known = command_word_detail(argv[off:])
        j, moved = j + off, moved or m
        if not known or base != "xargs":
            return j, base, moved, known, runs
        p = parse_xargs(argv, j)
        if p is None:
            return j, base, moved, False, runs
        runs.append(p)
        if p[0] >= len(argv):
            return len(argv), "", moved, True, runs
        off = p[0]


def with_xargs_input(args, replace):
    """args as xargs hands them on: each -I string replaced by XARGS_INPUT, or,
    with none, XARGS_INPUT appended.

    Appended twice. The input may be several words, and a command that reads
    its last operand apart from the rest -- rsync's destination, the remote
    command after ssh's host -- must find input on both sides of that split.
    """
    if replace is None:
        return list(args) + [XARGS_INPUT, XARGS_INPUT]
    return [a.replace(replace, XARGS_INPUT) for a in args]


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
LITERAL_CHAR = r"[^\s'\"\\;&|()<>$`~" + SENTINEL + r"]"
BINDING_RE = re.compile(
    r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)=(" + LITERAL_CHAR + r"*)"
    r"[ \t]*(;|&&|\Z)")
# Statements that may stand before the leading run of bindings without breaking
# it, because none can write a variable: the empty one mask_opaque leaves for a
# comment or blank line; `set` with flag-only arguments (an `o` bundle takes
# the option name after it, and zsh's `A` is left out: `set -AW` empties the
# array W); and `cd` (or zsh's chdir) to a literal -- which writes only PWD
# and OLDPWD, both DANGEROUS_VARS. Each must be followed by `;`. After `cd X && S=/x; rm -rf
# $S/y`, a failed cd skips the assignment and the `;` still runs the rm with S
# unset, as `rm -rf /y` -- and `set` fails too, on a letter or an option name
# the shell does not know (`set -Q`, `set -o nosuch`).
PRELUDE_RE = re.compile(
    r"[ \t]*(?:"
    r"set(?:[ \t]+(?:[-+][B-Za-z]*o[ \t]+[A-Za-z]+|[-+][B-Za-z]+))+[ \t]*;"
    r"|(?:cd|chdir)[ \t]+[^\s'\"\\;&|()<>$`~*?\[{}" + SENTINEL + r"]+[ \t]*;"
    r"|;)")
# ...and the same charset over an assignment TOKEN's value, for the names a line
# assigns outside the leading run. Those values may condemn and never clear.
LITERAL_VALUE_RE = re.compile(r"^" + LITERAL_CHAR + r"*$")


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
    command position and only on a line with danger -- and a bail is an ask,
    while clearing is decided here.

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
    `xargs` cannot rebind the parent shell at all. They are refused because
    they were in analyse()'s bail-out set when this shared it, and taking them
    out of the bail-out was not a reason to loosen this; see INDIRECTION.
    """
    words = re.split(r"[\s;&|()<>$]+", rest)
    try:
        words += [w.lstrip("$") for w in tokenize(rest)]
    except ValueError:
        return True
    return any(os.path.basename(w) in INDIRECTION for w in words)


def _home():
    """The hook's $HOME when it can be used as a fact: absolute, and not `/`.

    normpath keeps a leading `//` (POSIX leaves it implementation-defined), and
    on Linux that is `/` too, so every all-slash spelling counts as root.
    """
    h = os.environ.get("HOME", "")
    if not h or not posixpath.isabs(h):
        return None
    h = posixpath.normpath(h)
    return None if not h.strip("/") else h


def _zsh_suffixed(m):
    """Is this SUBST_NAME_RE match an unbraced $NAME that zsh reads on into?

    zsh takes what follows an unbraced name as part of the expansion: `$W:h`
    is W's head and `$W[1,1]` its first character, so `W=/tmp/ab/c; rm -rf
    $W:h:h:h/*` is rm -rf //* there and `$W[1,1]usr` is /usr. bash reads
    both as W's value and literal text. Such a word resolves to no single
    value: the bash reading may condemn, and nothing may clear.
    """
    return bool(m.group(2)) and m.string[m.end():m.end() + 1] in ("[", ":")


def _resolve_word(w, assigned, home=None):
    """A word built only from proven bindings and literal text -> that literal.

    -> None when anything in it is not a proven, non-empty binding, or the
    result is not a plain literal, or holds whitespace (an unquoted word
    splits there, and shlex has removed the quotes that would tell). A `~`
    is $HOME when home is given, so it resolves only through a HOME binding.
    This says what the word reads as, not whether the bindings still hold
    where it sits: callers gate on rebind_at.
    """
    if home and (w == "~" or w.startswith("~/")):
        w = "${HOME}" + w[1:]
    if _plain_literal(w) and not w.startswith("~"):
        return w

    def sub(m):
        val = (assigned or {}).get(m.group(1) or m.group(2))
        return val if val and not _zsh_suffixed(m) else m.group(0)
    out = SUBST_NAME_RE.sub(sub, w)
    if not _plain_literal(out) or out.startswith("~") or re.search(r"\s", out):
        return None
    return out


# A word that is one name's expansion and nothing else: `$B`, `${B}`, and
# `"$B"` once shlex has removed its quotes. Not `${=B}`, `$~B` or `${B:?}`.
NAME_WORD_RE = re.compile(
    r"^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$")
# Words the shell reads as syntax where they are typed, and as no more than a
# command name when an expansion produces them: `B=do; $B` opens no loop, and
# `B=noglob; $B rm *` finds no command noglob in zsh. The guard reads a word
# as typed, so it cannot stand in for one of these.
TYPED_SYNTAX = KEYWORDS | GROUPING_WORDS | {"[[", "]]", "time", "in", "noglob",
                                            "nocorrect", "-"}


def _typed_as_run(val):
    """Does this literal read, typed as a command word, as the shell runs it
    from an expansion? See _command_literal."""
    return bool(val) and not (
        val in TYPED_SYNTAX or ENV_ASSIGN_RE.match(val) or val[:1] in ("#", "=", "~")
        or val.isdigit() or not _plain_literal(val) or re.search(r"\s", val))


def _command_literal(w, assigned, home=None):
    """The literal a plain `$B` command word runs, or None.

    B must be a binding _resolve_word proves, non-empty, and its value one
    word that reads as typed exactly as the shell runs it: no whitespace or
    glob, where an unquoted $B splits and globs; no assignment, since `$B`
    with B=`A=1` runs a command named A=1; no word in TYPED_SYNTAX; and no
    leading `#` or `=` and no digits alone, which typed would be a comment,
    zsh's path lookup and a redirection's fd. Whether the binding still
    holds where w sits is the caller's question; see resolve_command_words.
    """
    if not NAME_WORD_RE.match(w):
        return None
    val = _resolve_word(w, assigned, home)
    return val if val is not None and _typed_as_run(val) else None


_UNSET = object()
# The prefix a command word read by its name alone stands under: an
# ellipsis, so the echoed segment shows a path the guard did not read.
UNREAD_PREFIX = chr(0x2026) + "/"
# How many outcomes of a line's commands line_values_at walks, at most.
MAX_OUTCOMES = 1 << 10


def line_values_at(toks, bounds, i, name, refused=frozenset(), unassigned=frozenset()):
    """-> every value name holds when the command at token i runs, from the
    line's own assignments before it, or None when one may come from
    elsewhere -- the environment, a rewrite -- or the line is not read.

    Only a word the shell reads as an assignment proves anything: not one
    whose NAME= quoting touched (unassigned: `"B=ls"`, `\\B=ls`, which run a
    command of that name), and nothing past an unquoted heredoc, whose body
    shlex hands over as words.

    The line up to i, or up to the first group, subshell, loop or branch
    before it, must be flat: joined only by `;`, `&&`, `||` and `|`, with no
    function defined anywhere on it. Each segment before i
    is an assignment statement -- NAME=VALUE words alone, which always
    succeed -- or a command, which may succeed or fail. Every outcome is
    walked as the shell runs the line, so `cd x && B=v; $B` may run with B
    unset, `B=v && $B` may not, and `w && B=a || B=b; $B` holds a or b. A
    command that may write name -- any mention but a plain read, eval,
    source or `.` -- leaves it unknown, and so does an assignment in a
    pipeline, which runs in a subshell. A name the line rewrites without
    naming it (refused: a nameref target, an unread set) or an IFS write
    before i reads nothing. Values come back as written.
    """
    if name in refused or ANY_NAME in refused or name in DANGEROUS_VARS:
        return None
    if any(t in ("<<", "<<-") for t in toks[:i]):
        return None
    # A word inside, or after, a group, loop, branch or subshell holds what
    # name held where that construct starts -- when nothing from there to
    # the line's end may rewrite it: not even after the word, since a loop
    # runs its body again. An assignment inside one is such a rewrite.
    stop = next((k for k in range(i + 1) if toks[k] in GROUPING_WORDS
                 or toks[k] in KEYWORDS or toks[k] in ("()", "time")), None)
    if stop is not None:
        rest = toks[stop:]
        text = " ".join(rest)
        if (any(t in ("()", "function") for t in rest)
                or not _read_only_mentions(name, text)
                or not _read_only_mentions("IFS", text)
                or any(command_name(t) in OPAQUE for t in rest)):
            return None
        i = stop
    segs = [(s, e) for s, e in bounds if s <= i]
    if not segs or not segs[-1][0] <= i < segs[-1][1]:
        return None
    ops, prev = [], 0
    for s, _ in segs:
        between = [t for t in toks[prev:s] if t not in (";", "\n")]
        if len(between) > 1 or any(t not in ("&&", "||", "|") for t in between):
            return None
        ops.append(between[0] if between else ";")
        prev = segs[len(ops) - 1][1]
    kinds = []
    for s, e in segs[:-1]:
        argv = toks[s:e]
        text = " ".join(argv)
        if not _read_only_mentions("IFS", text):
            return None
        if argv and all(ENV_ASSIGN_RE.match(t) and k not in unassigned
                        for k, t in enumerate(argv, s)):
            vals = [t.split("=", 1)[1] for t in argv if t.split("=", 1)[0] == name]
            kinds.append(("set", vals[-1] if vals else None))
            continue
        j, base = command_word(argv)
        kinds.append(("run", not _read_only_mentions(name, text) or base in OPAQUE))
    runs = [j for j, k in enumerate(kinds) if k[0] == "run"]
    if (1 << len(runs)) > MAX_OUTCOMES:
        return None
    seen = set()
    for bits in range(1 << len(runs)):
        fails = {j for n, j in enumerate(runs) if bits >> n & 1}
        val, status, ran = _UNSET, 0, True
        for j, op in enumerate(ops):
            if j == 0 or op == ";":
                go = True
            elif op == "|":
                go = ran
            elif op == "&&":
                go = status == 0
            else:
                go = status != 0
            if j == len(kinds):
                if go:
                    seen.add(val)
                break
            ran = go
            if not go:
                continue
            piped = op == "|" or ops[j + 1] == "|"
            kind, what = kinds[j]
            if kind == "set":
                status = 0
                if what is not None:
                    val = _UNSET if piped else what
            else:
                status = 1 if j in fails else 0
                if what:
                    val = _UNSET
    if not seen or _UNSET in seen:
        return None
    return sorted(seen)


def quoted_assignments(toks, masked):
    """-> the indexes of NAME=VALUE tokens whose `NAME=` quoting touched:
    `"B=ls"` and `\\B=ls` run a command of that name and assign nothing. A
    value's own quotes or substitution leave the assignment one. Every such
    token, when the masked text will not re-tokenize alike."""
    try:
        qtoks = tokenize(QUOTED_SPAN_RE.sub("QQQ", masked)) if masked else toks
    except ValueError:
        qtoks = None
    out = set()
    for k, t in enumerate(toks):
        if not ENV_ASSIGN_RE.match(t):
            continue
        q = qtoks[k] if qtoks is not None and len(qtoks) == len(toks) else ""
        if not q.startswith(t.split("=", 1)[0] + "="):
            out.add(k)
    return out


def _later_word(toks, bounds, i, refused, quoted, assigned=None, home=None,
                unassigned=frozenset()):
    """-> the words a plain `$B` command word at token i may run, through
    line_values_at, or None. A literal value runs as typed, and so does one
    the parser reads to a literal through ~ and proven bindings --
    `B=$HOME/bin/tool`. A value whose last component is a literal name under
    a prefix the parser cannot read -- `B=$ROOT/bin/tool` -- runs that name,
    when the use is quoted: the command is judged by its name, as a path
    with that basename is. An unquoted use may split on the prefix, and
    keeps its ask. More values than MAX_CWDS read none. quoted is the line's
    set of tokens quoting touched."""
    m = NAME_WORD_RE.match(toks[i])
    if not m:
        return None
    vals = line_values_at(toks, bounds, i, toks[i].strip("${}"), refused, unassigned)
    if not vals or len(vals) > MAX_CWDS:
        return None
    quoted = i in quoted
    out = []
    for v in vals:
        if not LITERAL_VALUE_RE.match(v):
            v = _resolve_word(v, assigned, home) or v
        if LITERAL_VALUE_RE.match(v):
            if not _typed_as_run(v):
                return None
            out.append(v)
            continue
        head, _, last = v.rpartition("/")
        if (not quoted or not head or not _typed_as_run(last)
                or not (EXPANSION_RE.search(head) or has_subst(head)
                        or SENTINEL in head)):
            return None
        out.append(UNREAD_PREFIX + last)
    return out


def resolve_command_words(toks, bounds, assigned, home, rebind_at, text="",
                          refused=frozenset(), quoted=frozenset(), picks=None,
                          unassigned=frozenset(), loose=False):
    """-> (toks, rebind_at, alts): each segment's command word that is a
    plain $B whose binding holds there replaced by B's literal, where the
    line's bindings stop being provable once the words it runs are known,
    and {index: candidates} for a word that may run more than one.

    A binding from the leading run is proven. Past it, line_values_at proves
    a literal the line assigns before the word, when it always runs first and
    nothing between may rewrite B (refused names it never may); a word that
    may hold either of two -- `w && B=a || B=b; $B` -- runs picks' choice,
    the first by default, and the caller reads the line once per choice. A
    quoted word whose proven value ends in a literal name under an unreadable
    prefix runs by that name; see _later_word.

    `B=/x/bin/tool; "$B" args` runs /x/bin/tool, so the segment is judged as
    if that were typed: an rm, a shell's -c script, an xargs, a wrapper and
    an eval are all read as such. The command word is effective_command's,
    and read again after each replacement, so `S=sudo; R=rm; $S $R -rf x`
    reaches the rm. The fences are the clearing gates: a word past rebind_at
    keeps its ask, and rebind_at is 0 for text another shell re-reads. The
    caller skips a line where a quote glues a name to a word. A word AT
    rebind_at is that rebinding itself, and its name is still bound when it
    is read: `E=eval; $E "$C"` is the eval the bail-outs then refuse.

    A replaced word may itself rebind a name without writing it -- a builtin
    that computes one, or eval, source and `.` -- so _rebinding_possible is
    asked again over the words as they will run, the indirections standing
    in as one of its builtins so a loop around them is placed too, and every
    replacement past the new rebind_at goes back to the expansion.

    The last value, hint, is True when a word stayed unproven though the
    line assigns its name a literal somewhere -- after a cd that may fail,
    in a branch, or quoted, `"B=rm"`. loose reads every such word as each
    of those literals, with no fence: a reading that only condemns, which
    the caller keeps for its denies.
    """
    if rebind_at <= 0 and not loose:
        return toks, rebind_at, {}, False
    out, done, alts, hint = list(toks), {}, {}, False
    for start, end in bounds:
        while True:
            i = start + effective_command(out[start:end])[0]
            if i >= end or (i > rebind_at and not loose) or i in done:
                break
            val = _command_literal(out[i], assigned, home)
            if val is None and NAME_WORD_RE.match(out[i]) \
                    and out[i].strip("${}") not in assigned:
                name = out[i].strip("${}")
                cands = _later_word(out, bounds, i, refused, quoted, assigned, home,
                                    unassigned)
                seen = list(dict.fromkeys(
                    t.split("=", 1)[1] for t in toks
                    if ENV_ASSIGN_RE.match(t) and t.split("=", 1)[0] == name
                    and _typed_as_run(t.split("=", 1)[1])))
                hint = hint or (not cands and bool(seen))
                if loose and not cands:
                    cands = seen
                # Every reading is read, or none: past MAX_CWDS of them the
                # word keeps its ask.
                if cands and len(cands) * _product(
                        len(c) for c in alts.values()) <= MAX_CWDS:
                    alts[i] = cands
                    val = cands[(picks or {}).get(i, 0) % len(cands)]
            if val is None:
                break
            done[i] = out[i]
            out[i] = val
    if not done:
        return toks, rebind_at, {}, hint
    if loose:
        return out, rebind_at, alts, hint
    probe = [("declare" if k in done and command_name(t) in INDIRECTION else t)
             for k, t in enumerate(out)]
    again = _rebinding_possible(probe, text)
    if again is not None:
        rebind_at = min(rebind_at, again[0])
    for k, w in done.items():
        if k > rebind_at:
            out[k] = w
            alts.pop(k, None)
    if all(k > rebind_at for k in done):
        return toks, rebind_at, {}, hint
    return out, rebind_at, alts, hint


def _literal_export(toks, i):
    """`export NAME` / `export NAME=value`, every argument spelled out.

    That writes only names written in the text, which _read_only_mentions
    already polices. declare, typeset, local and readonly stay refused: each
    takes -n, a nameref, which writes a name it never spells. A value with a
    substitution or a `$[` is refused too, since `$((W=1))` is arithmetic
    that assigns.
    """
    j = i + 1
    while j < len(toks) and toks[j] not in SEPARATORS:
        a = toks[j]
        if not (BARE_NAME_RE.match(a)
                or (ENV_ASSIGN_RE.match(a) and not has_subst(a) and "$[" not in a)):
            return False
        j += 1
    return j > i + 1


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
        p = PRELUDE_RE.match(masked, pos)
        if not p:
            break
        pos = p.end()
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
# zsh's `set -A name ...` fills an array, and so does `set -AW`, which takes
# the rest of its bundle as the name.
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
        `set` with a non-flag     zsh's `set -A name ...`, or `-AW` bundled.
        argument                  set -u, set -e and set -o pipefail are all
                                  flags and still clear.

    One write is stopped here although it names its target: IFS. It rebinds
    no binding, but changes what every later unquoted one reads as.

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
        # IFS decides where every later unquoted expansion splits, so a line
        # that writes it -- by any spelling, an assignment token included --
        # changes what each binding reads as from there on: after `IFS=/`,
        # bash splits $W/x with W=/tmp/usr into tmp, usr and x.
        if not _read_only_mentions("IFS", t):
            at, reason = i, "a write to IFS changes how a later unquoted expansion splits"
            break
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
                w = toks[j]
                if w in REDIRECTS:
                    j += 2
                    continue
                # zsh's A, alone or in a bundle, names an array to fill:
                # `set -AW` empties W.
                if not SET_FLAG_RE.match(w) or "A" in w:
                    at, reason = i, "`set` with a non-flag argument can assign"
                    break
                j += 2 if SET_OPT_ARG_RE.match(w) else 1
            if at is not None:
                break
            continue
        words = [os.path.basename(w) for w in TOKEN_WORDS_RE.split(t) if w]
        hit = next((w for w in words
                    if w in REBINDING_BUILTINS or w == "set"), None)
        # A literal export writes only the names it spells, which
        # _read_only_mentions has already judged; see _literal_export.
        if hit == "export" and t == "export" and _literal_export(toks, i):
            continue
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
# ...the same, as the letters of a bundle: `read -rp PROMPT`, `read -ra NAME`.
READ_VALUE_CHARS = {o[1] for o in READ_VALUE_OPTS}
# A word read may set: a plain name, or zsh's `name?prompt`.
READ_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\?.*)?$", re.S)
# cd's and pushd's flags, none of which takes a value: bash's -L -P -e -@ and
# zsh's -q (no chpwd hooks) and -s (refuse symlinks). The word after them is
# still the target. Any other option leaves the shell unplaceable.
CD_OPTS = {"-L", "-P", "-e", "-@", "-q", "-s"}
CD_FLAGS_RE = re.compile(r"^-[%s]+$" % "".join(o[1] for o in sorted(CD_OPTS)))
# `pushd +1`, `cd -2`: an entry on the directory stack, not a path.
DIR_STACK_INDEX_RE = re.compile(r"^[+-]\d+$")
GROUPING = {"(", ")", "{", "}"}


def _plain_literal(w):
    """A word with no expansion, substitution, glob, brace or masked span."""
    return not (EXPANSION_RE.search(w) or has_subst(w) or SENTINEL in w
                or any(c in w for c in GLOB_CHARS) or "{" in w)


class OutsideLoop(list):
    """A loop's words as a command outside that loop's body sees them.

    The loop may never have run there -- it sits in a function nobody calls,
    behind `false &&`, in an `if` not taken, in a subshell or a pipeline -- and
    then its name holds whatever it held before: `f() { for d in /tmp/y; do :;
    done; }; rm -rf "$d"/*` is rm -rf /*. So the words may condemn, never clear.
    """


class SplitWords(list):
    """A loop's words where an unquoted use may split them into others.

    A word holding whitespace -- `for C in "/tmp/x /usr"` -- is one path to
    `rm -rf "$C"` and two to `rm -rf $C` in bash (and zsh under SH_WORD_SPLIT),
    and shlex has removed the quotes that would tell the uses apart. A line
    that writes IFS splits any word. So the words, and each word's whitespace
    fields, may condemn; none clears.
    """


def _quoted_and_starts(toks, masked=""):
    """-> (the tokens quoting touched, the tokens a command starts at), or
    None when the masked text does not re-tokenize to the same tokens."""
    # shlex erases quotes, so `";"`, `\(`, `\;` and `"do"` arrive as bare
    # separators and keywords. Re-tokenizing the masked text with every quoted
    # or escaped span replaced by a plain word keeps the token boundaries and
    # marks each token quoting touched; such a token is an ordinary word below.
    try:
        qtoks = (tokenize(QUOTED_SPAN_RE.sub("QQQ", masked)) if masked else toks)
    except ValueError:
        return None
    if len(qtoks) != len(toks):
        return None
    quoted = {i for i, (a, b) in enumerate(zip(toks, qtoks)) if a != b}
    # Where a command starts: the line's start, after a separator or a
    # grouping token, or after a keyword (or `time`) that itself sits at a
    # start -- `echo do for x` and `[ ! for = x ]` put the keyword in an
    # argument, and the `for` after it opens nothing. `function f {` too.
    starts = set()
    for i in range(len(toks)):
        prev = toks[i - 1] if i and i - 1 not in quoted else None
        if (i == 0 or prev in SEPARATORS or prev in ("(", ")", "()")
                or ((prev in KEYWORDS or prev == "time") and i - 1 in starts)
                or (prev == "{" and i >= 3 and toks[i - 3] == "function"
                    and i - 3 in starts and i - 3 not in quoted)):
            starts.add(i)
    return quoted, starts


def loop_bodies(toks, masked=""):
    """-> the tokens that may run more than once: inside a loop of any kind,
    or after a function definition, which runs its body at every call.

    The opposite reading to loop_scopes', because a missed end costs the
    other way here. A loop opens and closes only on an unquoted word where a
    command starts, so `echo done` and `"done"` close nothing; a closer that
    is missed -- one written straight after `fi` -- keeps the body open to
    the line's end, which costs a prompt. Text that will not re-tokenize
    reads as all body.
    """
    read = _quoted_and_starts(toks, masked)
    if read is None:
        return set(range(len(toks)))
    quoted, starts = read
    body, stack = set(), []
    for i, t in enumerate(toks):
        if stack:
            body.add(i)
        if t in ("function", "()") or (t == "(" and toks[i + 1:i + 2] == [")"]):
            return body | set(range(i, len(toks)))
        if i in quoted or i not in starts:
            continue
        if t in LOOP_OPENERS:
            stack.append(LOOP_CLOSER_FOR[t])
        elif stack and t == stack[-1]:
            stack.pop()
    return body


def loop_spans(toks, masked=""):
    """-> [(opener, closer)] for every loop the line opens, read as
    loop_bodies reads them; one never closed runs to the line's end, and
    text that will not re-tokenize is one loop over all of it."""
    read = _quoted_and_starts(toks, masked)
    if read is None:
        return [(0, len(toks))]
    quoted, starts = read
    spans, stack = [], []
    for i, t in enumerate(toks):
        if i in quoted or i not in starts:
            continue
        if t in LOOP_OPENERS:
            stack.append((i, LOOP_CLOSER_FOR[t]))
        elif stack and t == stack[-1][1]:
            spans.append((stack.pop()[0], i))
    return spans + [(o, len(toks)) for o, _ in stack]


def defines_function(toks):
    """Does the line define a function, whose body runs at every call?"""
    return any(t in ("function", "()") or (t == "(" and toks[i + 1:i + 2] == [")"])
               for i, t in enumerate(toks))


def loop_scopes(toks, masked=""):
    """-> {name: [(opener, closer)]}: the span of each `for` or `select` loop
    that binds name.

    The two ends fail in opposite directions, so they are read differently. An
    opener counts only where a command starts -- the line's start, after a
    separator, or after a keyword or grouping word -- because a phantom one
    (`echo for d in /tmp/y`) pairs with a real closer and widens a span. A
    closer of the open loop's kind counts anywhere: one that is really data
    (`echo done`) only ends a span early, while one the shells accept after
    `esac`, `fi`, `done` or `)` in the same segment, missed, would let the
    inner span run on into its parent's body.
    """
    read = _quoted_and_starts(toks, masked)
    if read is None:
        return {}
    quoted, starts = read
    scopes, stack = {}, []
    for i, t in enumerate(toks):
        if t in LOOP_OPENERS:
            if i not in starts or i in quoted:
                continue
            name = (toks[i + 1] if t in ("for", "select") and i + 1 < len(toks)
                    and BARE_NAME_RE.match(toks[i + 1]) else None)
            stack.append((i, LOOP_CLOSER_FOR[t], name))
        elif t in LOOP_CLOSERS and stack and t == stack[-1][1]:
            start, _, name = stack.pop()
            if name:
                scopes.setdefault(name, []).append((start, i))
    return scopes


def loops_at(loops, scopes, pos):
    """The loop map as the command at token pos sees it: a name's words stand
    only inside the body of a loop that binds it, and are OutsideLoop
    elsewhere."""
    return {n: (ws if ws is None or any(a < pos < b for a, b in scopes.get(n, ()))
                else OutsideLoop(ws))
            for n, ws in loops.items()}


def loop_bindings(toks, resolve=None):
    """-> {name: [literal words] or None} for every name a loop binds.

    None means "bound to something not in this command" -- `while read f`, or a
    `for` list built from a glob or a substitution. That is a refusal, not an
    omission: leaving such a name out of the map would restore the bare-expansion
    allow, which is the exact thing this exists to withdraw.

    A name bound twice collapses to the union of both lists, so a second loop
    cannot quietly replace a first one's values with gentler ones.

    resolve(word, index) -> literal or None lets a word built from proven
    bindings count as visible, when the caller can vouch for them at index.
    A word it cannot resolve is read as it always was: a plain literal stands
    as written -- `~` included, which classify_one still condemns -- and
    anything else refuses the list.
    """
    out = {}

    def readable(w, at):
        got = resolve(w, at) if resolve is not None else None
        if got is not None:
            return got
        return w if _plain_literal(w) else None

    def bind(name, words):
        if name in out and (out[name] is None or words is None):
            out[name] = None
        elif name in out:
            merged = out[name] + [w for w in words if w not in out[name]]
            split = isinstance(out[name], SplitWords) or isinstance(words, SplitWords)
            out[name] = SplitWords(merged) if split else merged
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
                words = [readable(w, i) for w in words]
                if not words or any(w is None for w in words):
                    bind(name, None)
                elif any(re.search(r"\s", w) for w in words):
                    fields = [f for w in words for f in w.split() if f not in words]
                    bind(name, SplitWords(words + list(dict.fromkeys(fields))))
                else:
                    bind(name, words)
            else:
                bind(name, None)       # `for f; do` iterates "$@"
        elif os.path.basename(t) == "read":
            # Every plain name after read, past option words only, may be one
            # it sets. zsh's -t, -p and -k take no value, so `read -t W` reads
            # into W where bash's getopt reading below takes W as -t's value;
            # zsh's `name?prompt` names one too. Over-binding costs a prompt.
            j = i + 1
            while j < len(toks) and toks[j] not in SEPARATORS:
                w = toks[j]
                if w in REDIRECTS or REDIRECT_TOKEN_RE.match(w):
                    j += 2
                    continue
                m = READ_NAME_RE.match(w)
                if m and not w.startswith("-"):
                    bind(m.group(1), None)
                j += 1
            # Options as getopt reads them: in a bundle, the first letter that
            # takes a value takes the rest of the bundle, or the next word.
            # -a's value is an array the read fills, and a read that names
            # nothing at all fills REPLY.
            j, named = i + 1, False
            while j < len(toks) and toks[j].startswith("-") and toks[j] != "-":
                opt = toks[j]
                j += 1
                if opt == "--":
                    break
                for n, c in enumerate(opt[1:], 2):
                    if c in READ_VALUE_CHARS:
                        value = opt[n:]
                        if not value and j < len(toks) and toks[j] not in SEPARATORS:
                            value = toks[j]
                            j += 1
                        if c == "a" and BARE_NAME_RE.match(value):
                            bind(value, None)
                            named = True
                        break
            while (j < len(toks) and toks[j] not in SEPARATORS
                   and BARE_NAME_RE.match(toks[j])):
                bind(toks[j], None)
                named = True
                j += 1
            if not named:
                bind("REPLY", None)
    return out


# mapfile's and readarray's options that take a value, so the value is not
# read as the array's name.
MAPFILE_VALUE_OPTS = {"-d", "-n", "-O", "-s", "-u", "-C", "-c"}
# An assignment ENV_ASSIGN_RE does not match: `W+=x` appends, `W[0]=x` sets an
# element -- and $W is element 0.
UNREAD_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[[^]]*\]\+?|\+)=")
NAME_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")
# declare/typeset/local -n: the value is the NAME of another variable, which
# $W then reads through.
NAMEREF_FLAG_RE = re.compile(r"^-[A-Za-z]*n[A-Za-z]*$")
# The name an unread set may write when the guard cannot tell which: a
# nameref whose target is computed. It stands for every name.
ANY_NAME = "*"
# A parameter expansion that assigns: ${W:=x} sets an empty or unset W, ${W=x}
# only an unset one, and zsh's ${W::=x} always.
ASSIGN_DEFAULT_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:::=|:=|=)")


def unread_sets(toks):
    """-> [(index, name, by_builtin)] for each name this line sets in a way
    the assignment scan cannot read.

    A builtin that writes a name given as an argument: printf -v (and zsh's
    print -v), mapfile and readarray (MAPFILE when none is named), getopts
    (and OPTARG), and a declare -n reference, whose literal value is another
    variable's name. Those are by_builtin, and index is their segment's
    start. Then, at their own token: an assignment of a shape ENV_ASSIGN_RE
    does not match -- an append, an element, an array `W=(...)` -- and an
    assigning parameter expansion, `${W:=x}`, `${W=x}` or `${W::=x}`. Those
    are told by the token's shape, and shlex has already removed the quotes
    that would make `'W+=x'` a command name and `'${W:=x}'` plain text, so
    nothing may rest on their having run. read is loop_bindings'.

    A reference's target is set too, at the token that names it: what the
    line assigns the reference (`declare -n R=W`, or `R=W` after `declare -n
    R`) is the name every later `R=...` writes. Not by_builtin, so a
    literal the target held before still condemns.
    """
    out, refs = [], set()
    for s, e in segment_bounds(toks):
        whole = strip_redirections(toks[s:e])
        # Every command that starts in the segment: its command word, and one
        # after a `{`, `(`, `()` or keyword inside it -- `f() { local -n R=W`.
        firsts = [0] + [i for i in range(1, len(whole))
                        if whole[i - 1] in KEYWORDS or whole[i - 1] == "()"]
        seen = set()
        for st in firsts:
            argv = whole[st:]
            j, base = command_word(argv)
            if st + j in seen:
                continue
            seen.add(st + j)
            args = argv[j + 1:]
            if base in ("printf", "print"):
                name = print_v_word(args)
                m = NAME_PREFIX_RE.match(name) if name is not None else None
                if m:
                    out.append((s, m.group(0), True))
            elif base in ("mapfile", "readarray"):
                names, k = [], 0
                while k < len(args):
                    if args[k] in MAPFILE_VALUE_OPTS:
                        k += 2
                        continue
                    if BARE_NAME_RE.match(args[k]):
                        names.append(args[k])
                    k += 1
                out.append((s, names[-1] if names else "MAPFILE", True))
            elif base == "getopts":
                out.append((s, "OPTARG", True))
                if len(args) >= 2 and BARE_NAME_RE.match(args[1]):
                    out.append((s, args[1], True))
            elif base == "nameref" or (base in ("declare", "typeset", "local") and
                                       any(NAMEREF_FLAG_RE.match(a) for a in args)):
                names = [m.group(0) for m in (NAME_PREFIX_RE.match(a) for a in args)
                         if m]
                out.extend((s, n, True) for n in names)
                refs.update(names)
    # ...and the name each reference refers to, which an assignment through
    # the reference writes without naming: `declare -n R=W; R=/` sets W. Its
    # target is whatever the line assigns the reference, on the declaration
    # or after it: `declare -n R; R=W`.
    for k, t in enumerate(toks):
        if ENV_ASSIGN_RE.match(t) and t.split("=", 1)[0] in refs:
            value = t.split("=", 1)[1]
            m = NAME_PREFIX_RE.match(value)
            # A target the guard cannot read may be any name: ANY_NAME.
            if EXPANSION_RE.search(value) or has_subst(value) or SENTINEL in value:
                out.append((k, ANY_NAME, False))
            elif m:
                out.append((k, m.group(0), False))
    for k, t in enumerate(toks):
        m = UNREAD_ASSIGN_RE.match(t)
        if m:
            out.append((k, m.group(1), False))
        elif ENV_ASSIGN_RE.match(t) and t.endswith("=") and toks[k + 1:k + 2] == ["("]:
            out.append((k, t[:-1], False))
        out.extend((k, m.group(1), False) for m in ASSIGN_DEFAULT_RE.finditer(t))
    return out


def names_set_unread(toks):
    """-> the names unread_sets finds.

    Each name is opaque to classify_one: its value came from this line, so
    the free passes that assume nothing chose it do not hold.
    """
    return {name for _, name, _ in unread_sets(toks)}


def _overwrites_before(toks, bounds, k):
    """Does the unread set at token k run, in this shell, before every later
    segment does?

    Its segment must start the line or follow a `;`, and end it or lead into
    a `;` or `&&`: after `&&` or `||` it may not run, and a pipeline or `&`
    runs it in a subshell. Nothing on the line may group, loop or branch,
    and a set written inside a substitution runs in one. Used only to stop a
    literal condemning, so a miss here keeps the literal: the strict side.
    """
    if any(t in GROUPING_WORDS for t in toks) or has_subst(toks[k]):
        return False
    for s, e in bounds:
        if s <= k < e:
            before = toks[s - 1] if s else ";"
            after = toks[e] if e < len(toks) else ";"
            return before == ";" and after in (";", "&&")
    return False


def computed_target(argv, j):
    """-> the argument through which the builtin at argv[j] assigns a name it
    computes, or None.

    `declare "$N"=/usr`, `printf -v "$N" x`, `read "$N"`: the name W arrives
    as data, so no scan of this line can say W was set. let evaluates its
    arguments as arithmetic, which assigns through whatever an expansion in
    them holds, so any expansion there counts.
    """
    base = command_name(argv[j])
    args = strip_redirections(argv[j + 1:])

    def computed(w):
        return "$" in w or "`" in w or SENTINEL in w

    # A nameref's value is the name every later write through it lands on:
    # `declare -n R=$N` computes it too.
    if base == "nameref" or (base in ("declare", "typeset", "local")
                             and any(NAMEREF_FLAG_RE.match(a) for a in args)):
        hit = next((a for a in args if "=" in a and computed(a.split("=", 1)[1])), None)
        if hit is not None:
            return hit
    if base in ("declare", "typeset", "local", "export", "readonly", "unset"):
        return next((a for a in args if not a.startswith(("-", "+"))
                     and computed(a.split("=", 1)[0])), None)
    if base == "let":
        return next((a for a in args if computed(a)), None)
    if base == "read":
        return next((a for a in args if not a.startswith("-") and computed(a)), None)
    if base in ("mapfile", "readarray"):
        k = 0
        while k < len(args):
            if args[k] in MAPFILE_VALUE_OPTS:
                k += 2
                continue
            if not args[k].startswith("-") and computed(args[k]):
                return args[k]
            k += 1
        return None
    if base in ("printf", "print"):
        name = print_v_word(args)
        return name if name is not None and computed(name) else None
    if base == "getopts" and len(args) >= 2 and computed(args[1]):
        return args[1]
    return None


# zsh print's option letters that take a value (bash's printf has only -v).
PRINT_VALUE_CHARS = set("CfuvxX")
OPTION_WORD_RE = re.compile(r"^-[A-Za-z]")


def print_v_word(args):
    """-> the word printf's or print's -v assigns through, or None.

    Options come first and end at the first word that is not one: the
    format, or prose. Reading every word for a `-v` took `printf '%s\\n'
    '- a -v b'` for one. `-vNAME` carries the word, `-v NAME` the next one.
    """
    k = 0
    while k < len(args) and OPTION_WORD_RE.match(args[k]):
        a = args[k]
        for m in range(1, len(a)):
            if a[m] in PRINT_VALUE_CHARS:
                value = a[m + 1:] or (args[k + 1] if k + 1 < len(args) else "")
                if a[m] == "v":
                    return value
                k += 1 if a[m + 1:] else 2
                break
        else:
            k += 1
    return None


# A relative cd target is looked up on CDPATH -- zsh's cdpath, tied to it --
# and under bash's cdable_vars or zsh's CDABLE_VARS (`set -T` there) a target
# that is no directory reads as a variable naming one: `CDPATH=/; cd usr` is
# in /usr. A line that names either, or sets zsh's -T flag, may search.
CD_SEARCH_NAME_RE = re.compile(r"(?<![A-Za-z0-9_])(?:CDPATH|cdpath)(?![A-Za-z0-9_])")
CDABLE_FLAG_RE = re.compile(r"^[-+][A-Za-z]*T[A-Za-z]*$")
OPTION_SETTERS = {"set", "setopt", "unsetopt", "emulate"}
# More places than this for the shell to stand, and the next relative cd
# leaves it unplaceable rather than multiplying them again.
MAX_CWDS = 64


def cd_may_search(cmd, toks):
    """Can a relative cd target on this line be looked up somewhere other
    than the working directory? See CD_SEARCH_NAME_RE.

    Read off the text with its quotes and backslashes gone, as the shell
    reads `export C""DPATH=/`; zsh ignores case and underscores in an option
    name. An option setter or a shell handed an expansion may be handed -T.
    A mention that only reads CDPATH counts too, which costs a prompt.
    """
    plain = re.sub(r"['\"\\]", "", cmd)
    if (CD_SEARCH_NAME_RE.search(plain)
            or "cdablevars" in plain.replace("_", "").lower()):
        return True
    for start, end in segment_bounds(toks):
        argv = [t for t in toks[start:end] if t not in GROUPING]
        j, base = command_word(argv)
        if base not in OPTION_SETTERS and base not in SHELLS:
            continue
        for t in argv[j + 1:]:
            if t == "--":
                break
            if CDABLE_FLAG_RE.match(t) or (base in OPTION_SETTERS and (
                    has_subst(t) or EXPANSION_RE.search(t))):
                return True
    return False


def _cdpath_set():
    """Does the hook's own environment hold a CDPATH with an entry other
    than the working directory (empty or `.`)?"""
    return any(e and posixpath.normpath(e) != "."
               for e in os.environ.get("CDPATH", "").split(":"))


def candidate_cwds(toks, cwd, resolve=None, searches=False, masked=""):
    """-> every directory a relative operand in this command can resolve
    against: cwd_readings, each once."""
    return list(dict.fromkeys(cwd_readings(toks, cwd, resolve, searches, masked)))


def cwd_readings(toks, cwd, resolve=None, searches=False, masked="", marks=None):
    """-> every directory a relative operand in this command can resolve
    against, the session's own cwd first and alone as the reading that no cd
    has run. A cd whose target is that cwd adds it again, as its own reading.
    marks, when given, is filled with len(out) as each segment_bounds
    segment starts and once after the last, so a caller can tell which
    readings the cds before a command added; see _analyse's cwds_at.

    `cd` (and zsh's `chdir`, the same builtin), `pushd` and `popd` are the
    only builtins in a pipeline that move the shell this rm runs in, and not
    tracking them is why `cd /; rm -rf
    build` and `pushd / && rm -rf usr` cleared: the operand was judged
    against the *hook's* cwd, where it is bounded, instead of against `/`. A
    child process cannot move us, so every one of them in the command text is
    a candidate. Zoxide's functions move it too, somewhere no text names, so
    each one where a command starts makes the shell unplaceable; see
    ZOXIDE_MOVERS. Other hidden movers are a known gap.

    Both the before and the after are candidates, not only the after --
    `cd /tmp; rm -rf ./y` runs the rm where it started if the cd fails.
    classify_operand judges all of them and takes the worst, which covers
    the conditional case without having to decide whether it fires. The one
    exception is decided per command, by the caller: one that runs only if
    a cd succeeded drops the before; see cd_must_have_run.

    resolve(word, index) is loop_bindings': a target built from bindings that
    hold at the cd -- `S=/tmp/s; cd $S` -- places the shell. Only an absolute
    result does, since a relative one is looked up on CDPATH. `~` and a bare
    `cd` go to $HOME, which places the shell only through a HOME binding; a
    `~` target keeps its literal reading too, as a directory named `~` -- what
    `cd "~"` means, and shlex has removed the quotes.

    A relative target is joined to every place the shell may stand by then,
    not only to where it started: `cd /tmp/a && cd ../../etc` is in /etc.
    Those readings stay, and the shell is unplaceable as well, when the
    target may be looked up elsewhere -- searches is cd_may_search's finding,
    and the hook's own CDPATH counts too -- or when it climbs with `..` and
    may run more than once: `while ...; do cd ..; done` climbs as far as it
    runs. masked is the line as mask_opaque leaves it; see loop_bodies.

    A cd the segments do not read as one -- in a one-line function body,
    `f() { cd /; }`, or behind a `);` shlex keeps as one token, `x=(a); cd
    /` -- still moves the shell, to a place this does not read, so every
    relative operand on the line asks.
    """
    searches = searches or _cdpath_set()
    repeats = loop_bodies(toks, masked)
    read_starts = _quoted_and_starts(toks, masked)
    starts = read_starts[1] if read_starts else set(range(len(toks)))
    out = [cwd]
    read = set()
    marks = [] if marks is None else marks
    for start, end in segment_bounds(toks):
        marks.append(len(out))
        at = [k for k in range(start, end) if toks[k] not in GROUPING]
        argv = [toks[k] for k in at]
        idx, base, moved, _ = command_word_detail(argv)
        if idx < len(at):
            read.add(at[idx])
        # zoxide's z goes wherever its database says. Its command word, or
        # one where a command starts inside the segment -- `f() { z p; }`.
        if base in ZOXIDE_MOVERS or any(
                k in starts and command_name(toks[k]) in ZOXIDE_MOVERS
                for k in range(start, end)):
            out.append(UNKNOWN_CWD)
        # ...and one behind xargs moves what xargs runs: `xargs env -C / rm`.
        moved = moved or effective_command(argv)[2]
        if moved:
            # `env -C / rm -rf ./usr` moves only the command it runs, and to
            # a place this does not follow. The candidates are the line's, so
            # every relative operand on it asks: a false ask on `env -C / ls;
            # rm -rf ./b`, which is rare.
            out.append(UNKNOWN_CWD)
        # A trap's action runs later, at a signal -- DEBUG before every
        # command -- so one that may move the shell leaves it unplaceable.
        if base == "trap" and _trap_may_move(argv[idx + 1:]):
            out.append(UNKNOWN_CWD)
            continue
        if base not in CD_WORDS:
            continue
        # Options first. One the guard does not know -- `pushd -n`, a typo --
        # may change what the next word means, so the shell is unplaceable.
        # `-` and a stack index (`-2`) are targets, not options. Redirections
        # are the shell's, wherever they stand: `cd 2>/dev/null /` goes to /.
        args, k, unknown = strip_redirections(argv[idx + 1:]), 0, False
        while (k < len(args) and args[k].startswith("-") and args[k] != "-"
               and not DIR_STACK_INDEX_RE.match(args[k])):
            if args[k] == "--":
                k += 1
                break
            if not CD_FLAGS_RE.match(args[k]):
                unknown = True
                break
            k += 1
        if unknown:
            out.append(UNKNOWN_CWD)
            continue
        args = args[k:]
        # pushd DIR moves the shell exactly as cd DIR does. popd, a bare
        # pushd and a stack index (+1, -2) go to a directory pushed earlier,
        # perhaps by another tool call, which nothing here can name.
        if base == "popd" or (base == "pushd" and not args) or (
                args and DIR_STACK_INDEX_RE.match(args[0])):
            out.append(UNKNOWN_CWD)
            continue
        # Two targets are zsh's `cd OLD NEW`, which swaps OLD for NEW in
        # $PWD: `cd /home/u/project /` lands in /. bash refuses the form.
        # The first one's reading stays a candidate too, as it always was.
        if len(args) > 1:
            out.append(UNKNOWN_CWD)
        target = args[0] if args else "~"
        got = resolve(target, start) if resolve is not None else None
        through = got is not None and got != target
        if through:
            out.append(posixpath.normpath(got) if posixpath.isabs(got)
                       else UNKNOWN_CWD)
        elif resolve is not None and target.startswith("~"):
            # A $HOME the line does not prove: it writes HOME, or a rebinding
            # may have run before the cd.
            out.append(UNKNOWN_CWD)
        if through and not target.startswith("~"):
            continue
        # As written: how every target read before resolve, and still the
        # only reading of one it leaves alone.
        if not args:
            out.append(os.environ.get("HOME", "") or UNKNOWN_CWD)
        elif target == "-" or not _plain_literal(target):
            out.append(UNKNOWN_CWD)
        elif posixpath.isabs(target):
            out.append(posixpath.normpath(target))
        else:
            # Each reading only adds a candidate, so a deny one of them finds
            # still stands beside the unknown.
            here = list(dict.fromkeys(out))
            moved_to = [posixpath.normpath(posixpath.join(c, target))
                        if c and posixpath.isabs(c) else UNKNOWN_CWD for c in here]
            if len(here) + len(moved_to) > MAX_CWDS:
                moved_to = moved_to[:1] + [UNKNOWN_CWD]
            out.extend(moved_to)
            # Only a target whose first component is `.` or `..` is never
            # looked up: `./usr` and `../usr` stay put, `usr/bin` and `.hid`
            # do not.
            if searches and target.split("/", 1)[0] not in (".", ".."):
                out.append(UNKNOWN_CWD)
            elif ".." in target.split("/") and idx < len(at) and at[idx] in repeats:
                out.append(UNKNOWN_CWD)
    marks.append(len(out))
    if any(command_name(t) in CD_WORDS and k not in read
           for k, t in enumerate(toks)):
        out.append(UNKNOWN_CWD)
    return out


def _trap_may_move(args):
    """Could the action of `trap ACTION SIGNAL...` move the shell?

    It may when it holds a cd, chdir, pushd, popd or a zoxide jump anywhere,
    or when its text cannot be read: an expansion or a substitution where a
    command starts, eval, source or `.`, or text that will not tokenize.
    `trap - SIG`, `trap '' SIG`, `trap -p` and an action that only runs
    commands -- `trap 'rm -rf "${T:?}"' EXIT`, `trap cleanup EXIT` -- move
    nothing. A function defined elsewhere is the documented gap.
    """
    args = [a for a in strip_redirections(args) if a not in ("-l", "-p", "--")]
    if not args or args[0] in ("", "-"):
        return False
    action = args[0]
    if has_subst(action) or SENTINEL in action:
        return True
    try:
        toks = tokenize(action)
    except ValueError:
        return True
    if any(command_name(t) in CD_WORDS or command_name(t) in ZOXIDE_MOVERS
           for t in toks):
        return True
    for s, e in segment_bounds(toks):
        j, base, _, known = command_word_detail(toks[s:e])
        if s + j >= e:
            continue
        word = toks[s + j]
        if not known or base in OPAQUE or "$" in word or has_subst(word):
            return True
    return False


# Functions the shell snapshot defines that move the shell: zoxide's, from
# `zoxide init zsh` in ~/.zshrc. `cd` itself stays the builtin in Claude
# Code's shell, but these are still defined, and go wherever zoxide's
# database ranks a match highest.
ZOXIDE_MOVERS = {"z", "zi", "__zoxide_z", "__zoxide_zi"}


# What may stand before a cd in its own segment and leave it a cd this shell
# runs, whose failure the `&&` after it sees: the start of a group or of a
# compound command's list, and `builtin`. Assignments too. Not `!`, which
# turns a failed cd into success, and no other wrapper -- `command -v cd`
# succeeds without moving anything.
CD_LEADERS = {"(", "{", "do", "then", "else", "elif", "if", "while", "until",
              "builtin"}
CHAIN_OPS = ("&&", "||", "|", "|&")
# A run of the punctuation shlex glues into one token: `)&&`, `;;`, `);`.
GLUED_OP_RE = re.compile(r"^[();<>|&]+$")


def _op_before(toks, s, quoted=frozenset()):
    """-> (the list operator in front of the segment starting at s, its
    index): `&&`, `||`, `|`, `;`, `&`, or ("", -1) at the line's start. A
    newline after `&&`, `||` or `|` continues the line, and tokenize has
    made it a `;`. A quoted token is a word, not an operator, whatever shlex
    made of it: ("quoted", index) then, which is no operator at all."""
    j = s - 1
    while j >= 0 and toks[j] in (";", "\n") and j not in quoted:
        j -= 1
    if j >= 0 and toks[j] in CHAIN_OPS and j not in quoted:
        return toks[j], j
    if s == 0 or j < 0:
        return "", -1
    if s - 1 in quoted:
        return "quoted", s - 1
    return toks[s - 1], s - 1


def _plain_cd(toks, s, e, quoted, starts):
    """Is toks[s:e] a cd, pushd or popd this shell runs, and nothing else?

    The cd word must be unquoted, and it or the assignments and `builtin`
    in front of it must stand where a command really starts: `echo ';' cd
    x` reads as two segments once shlex has dropped the quotes, and runs no
    cd. An option it does not know -- `pushd -n`, which moves nothing --
    makes it no plain cd either.
    """
    c = s
    while c < e and (toks[c] in CD_LEADERS or ENV_ASSIGN_RE.match(toks[c])):
        c += 1
    if c >= e or toks[c] not in CD_WORDS or c in quoted:
        return False
    j = c
    while j > s and (toks[j - 1] == "builtin" or ENV_ASSIGN_RE.match(toks[j - 1])):
        j -= 1
    if j not in starts or any(k in quoted for k in range(j, c + 1)):
        return False
    for t in toks[c + 1:e]:
        if t == "--":
            break
        if (t.startswith("-") and t != "-" and not CD_FLAGS_RE.match(t)
                and not DIR_STACK_INDEX_RE.match(t)):
            return False
    return _simple(toks[c + 1:e])


def _simple(seg):
    """No group opens or closes in it, and no operator shlex glued to a
    parenthesis hides one -- `cd /tmp/x)&&` is one token."""
    return not any(t in GROUPING or (GLUED_OP_RE.match(t) and not
                                     REDIRECT_TOKEN_RE.match(t)) for t in seg)


def cd_must_have_run(toks, bounds, k, read=None):
    """Does segment k run only if a cd before it succeeded?

    Then the shell is not where the session started -- that reading is the
    cd failing and the rest running anyway -- so it is dropped. Proven only
    for one shape: k is reached from a plain cd (_plain_cd) through `&&`
    alone, every command in between simple, and the cd itself starts its
    list or follows an `&&`. `a && cd x && rm` qualifies, since the cd runs
    and succeeds before rm can, whatever a did. None of these does, and each
    keeps the session's cwd: `cd x; rm`, `cd x | rm`, `cd x && a || rm`, `a
    || cd x && rm` (a succeeds, the cd never runs), `! cd x && rm`, `(cd x)
    && rm`, a cd behind any wrapper but `builtin` or with an option it does
    not know, a cd a quoted separator only appears to start (`echo ';' cd
    x && rm`), and a chain with a zoxide mover in it, `cd x && z p && rm`.

    read is _quoted_and_starts' (quoted, starts) for the line, which decides
    which separators and command starts are real; without it, False.
    """
    if read is None:
        return False
    quoted, starts = read
    i = k
    while True:
        op, at = _op_before(toks, bounds[i][0], quoted)
        if op != "&&" or i == 0 or bounds[i - 1][1] != at:
            return False
        i -= 1
        s, e = bounds[i]
        if any(command_name(t) in ZOXIDE_MOVERS for t in toks[s:e]):
            return False
        if _plain_cd(toks, s, e, quoted, starts):
            return _op_before(toks, s, quoted)[0] in ("", ";", "\n", "&", "&&")
        if not _simple(toks[s:e]):
            return False


# --- per-command target extraction -------------------------------------------

def find_targets(argv):
    """find: -> (destructive, roots, follows). Roots are the paths find
    descends from. follows names the flag that makes find follow symlinks
    below them -- a leading -L, or -follow anywhere in the expression -- or
    is None. Any -L counts, even one a later -H or -P overrides, which costs
    an ask. -H follows only a root that is itself a link, which the root's
    own judgement already covers wherever it reads the disk."""
    i, follows = 1, None
    while i < len(argv) and argv[i] in FIND_LEADING_OPTS:
        if argv[i] == "-L":
            follows = "find -L"
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
        elif t == "-follow":
            follows = follows or "find -follow"
        elif t in FIND_EXEC_ACTIONS and j + 1 < len(rest):
            if command_name(rest[j + 1]) in DESTRUCTIVE_EXEC:
                destructive = True
    return destructive, roots, follows


def fd_targets(argv):
    """fd: -> (destructive, roots, follows). fd's --exec is find's -exec.
    follows names --follow, or its -L alone or in a bundle of short flags,
    when one makes fd follow symlinks below its roots; else None."""
    positional, named, destructive, follows = [], [], False, None
    i = 1
    while i < len(argv):
        t = argv[i]
        if t in FD_EXEC_FLAGS:
            if i + 1 < len(argv) and command_name(argv[i + 1]) in DESTRUCTIVE_EXEC:
                destructive = True
            break  # everything after this is the exec'd command, not a path
        head = t.split("=", 1)[0]
        if t == "--follow":
            follows = "fd --follow"
        elif t.startswith("-") and not t.startswith("--") and "L" in t[1:]:
            follows = "fd -L"
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
    return destructive, roots, follows


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
    destination; purge/delete/rmdir destroy what they name. The subcommand
    comes back either way, so a caller can see one xargs supplied."""
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
        return False, [], sub
    rest = operands[1:]
    if sub in ("sync", "move"):
        return True, rest[-1:], sub  # destination is pruned to match source
    return True, rest, sub           # purge/delete/rmdir destroy what they name


def strip_redirections(argv):
    """-> argv without redirections. `2>/dev/null` is not three more operands.

    shlex splits a redirection into its own tokens. git reads no operands any
    more, but it still has to find its subcommand, and `git >/dev/null clean`
    put `>` where git_subcommand looks for one.
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


def git_relocates(text, toks, bounds):
    """-> does anything on this line move the tree a git clean would clean?

    Per LINE, not per segment: `export GIT_DIR=...; git clean -fdx` moves it
    from the segment before. And read twice, because each reading misses what
    the other sees. The raw text sees inside a masked substitution. The tokens
    see past quoting, which the shell strips before git looks at anything --
    `--work-tr""ee=/` reaches git as `--work-tree=/`, and no regex over the raw
    text can promise to undo every spelling of that.
    """
    if GIT_RELOCATE_RE.search(text):
        return True
    if any(GIT_RELOCATE_TOKEN_RE.search(t) for t in toks):
        return True
    # `git init` relocates by creating: the cwd becomes a repository with
    # nothing tracked, so a clean after it removes everything under the cwd.
    # Any git token, not command_word's: it stops at a wrapper's own option, so
    # `sudo -u root git init` would hide the init.
    for start, end in bounds:
        seg = strip_redirections(toks[start:end])
        for j, t in enumerate(seg):
            if (os.path.basename(t) in GIT_NAMES
                    and git_subcommand(seg[j:])[1] == "init"):
                return True
    return False


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


# Commands that run their arguments in a container, under exec or run.
CONTAINER_RUNNERS = {"docker", "podman", "nerdctl", "kubectl"}


def _in_container(prefix):
    """Do the words before an rm hand it to a container's exec or run?"""
    j, base = command_word(prefix)
    return base in CONTAINER_RUNNERS and any(t in ("exec", "run") for t in prefix[j + 1:])


def ssh_remote_command(argv):
    """ssh: -> the remote command string, or "" if this is an interactive login.

    OpenSSH reads options on both sides of the host: once it has the host it
    parses the words after it for options again, unless a `--` came before
    the host. A `--` after the host ends them and is dropped. So `ssh h -- CMD`
    and `ssh h -t CMD` both run CMD, and reading the `--` or the `-t` as the
    first word of the remote command hid what CMD was.
    """
    idx, _ = command_word(argv)
    i, host, ended = idx + 1, None, False
    while i < len(argv):
        t = argv[i]
        if not ended and t == "--":
            ended = True
            i += 1
            continue
        if not ended and t.startswith("-") and t != "-":
            i += ssh_option_width(t)
            continue
        if host is not None:
            break
        host = i
        i += 1
    if host is None or i >= len(argv):
        return ""
    rest = argv[i:]
    return rest[0] if len(rest) == 1 else " ".join(rest)


def ssh_option_width(t):
    """-> how many words the ssh option word t takes: 2 when its last letter
    wants a value (`-p 22`, `-tp 22`), else 1 (`-t`, `-p22`, `-tp22`)."""
    for k in range(1, len(t)):
        if t[k] in SSH_VALUE_CHARS:
            return 1 if k + 1 < len(t) else 2
    return 1


def _past_redirections(argv, j, end):
    """The index past any redirections starting at argv[j], stopping at end.

    The outer shell applies and removes them before the command runs, so
    `bash -c 2>/dev/null SCRIPT` hands bash `-c SCRIPT`. shlex splits
    `2>/dev/null` into three words, so an fd digit goes with its operator.
    """
    while j < end:
        k = j + 1 if ((argv[j].isdigit() or FD_BRACE_RE.match(argv[j]))
                      and j + 1 < end and REDIRECT_TOKEN_RE.match(argv[j + 1])) else j
        if not REDIRECT_TOKEN_RE.match(argv[k]) or k + 1 >= end:
            break
        j = k + 2
    return j


def shell_script(argv, i):
    """`SHELL [opts] -c SCRIPT ...` with the shell at argv[i] -> SCRIPT's index.

    None when this shell has no -c (it runs a file, or reads stdin). False
    when the script it runs cannot be read, which the caller turns into an
    ask:
      - a long option the guard does not know, which may take a value --
        `bash --rcfile -c x` runs the file x;
      - a -c found only past the point the scan stopped, as in `bash $OPTS -c`;
      - a script that is itself an expansion or a substitution -- `-c "$S"`,
        `-c "$(curl ...)"`, and `$'...'`, which shlex hands back as `$...`;
      - any fish script, which is not POSIX shell.
    """
    end = next((k for k in range(i + 1, len(argv)) if argv[k] in SEPARATORS),
               len(argv))
    if command_name(argv[i]) == "fish":
        return (False if any(FISH_SCRIPT_FLAG_RE.match(t) for t in argv[i + 1:end])
                else None)
    j, saw_c, unknown = i + 1, False, False
    while True:
        j = _past_redirections(argv, j, end)
        if j >= end:
            break
        t = argv[j]
        if t == "--":
            j += 1
            break
        if t.startswith("--"):
            unknown = unknown or t not in SHELL_LONG_FLAGS
            j += 1
            continue
        if SHELL_FLAG_RE.match(t):
            saw_c = saw_c or (t[0] == "-" and "c" in t[1:])
            j += 1 + t[1:].count("o") + t[1:].count("O")
            continue
        break
    if not saw_c and not any(SHELL_C_FLAG_RE.match(t) for t in argv[j:end]):
        return None
    if unknown or not saw_c:
        return False
    j = _past_redirections(argv, j, end)
    if (j >= end or REDIRECT_TOKEN_RE.match(argv[j])
            or argv[j].startswith(("$", "`"))):
        return False
    return j


def _reads_stdin(seg, j, bodies):
    """Does the shell at seg[j] read its stdin as its script?

    Only with no -c and nothing after it but short flags and heredocs. With a
    script operand, stdin is that script's data, not code. After `-s` every
    word is an argument and stdin is still the script. A `-` or `--` ends the
    options, as bash and zsh treat it: a word after one is the script FILE
    (`bash - x` runs x), and only with none is stdin read. fish is never
    read; see shell_script.
    """
    if command_name(seg[j]) == "fish" or shell_script(seg, j) is not None:
        return False
    args, opts_done, names = False, False, 0
    for t in seg[j + 1:]:
        if t in bodies or args:
            continue
        if names:                     # an `-o` or `-O` option's name
            names -= 1
            continue
        if not opts_done and t in ("-", "--"):
            opts_done = True
            continue
        if not opts_done and SHELL_FLAG_RE.match(t):
            args = t[0] == "-" and "s" in t[1:]
            names = t[1:].count("o") + t[1:].count("O")
            continue
        return False
    return True


def shell_stdin_script(toks, bounds, k, bodies):
    """-> (placeholder, here) for the quoted heredoc segment k's shell reads as
    its script, or None.

    here is True when the shell is the segment's command word. A shell
    further along -- `ssh h bash`, `sudo -E bash`, `docker exec -i c bash` --
    may run on another machine or in another filesystem, so the caller reads
    the body as the ssh far end reads its string. The body arrives in the
    segment itself, or from a `cat` that does nothing but print it into a
    pipe. `cat <<'EOF' | tee x | bash` is not followed past the tee.
    """
    s, e = bounds[k]
    seg = toks[s:e]
    j, base = command_word(seg)
    if base in SHELLS:
        if not _reads_stdin(seg, j, bodies):
            return None
        here, at = True, j
    else:
        at = next((n for n in range(j + 1, len(seg))
                   if command_name(seg[n]) in SHELLS
                   and _reads_stdin(seg, n, bodies)), None)
        if at is None:
            return None
        here = False
    own = [t for t in seg[at + 1:] if t in bodies]
    if own:
        return own[0], here
    if k > 0 and toks[bounds[k - 1][1]] == "|":
        ps, pe = bounds[k - 1]
        prev = toks[ps:pe]
        if len(prev) == 2 and os.path.basename(prev[0]) == "cat" and prev[1] in bodies:
            return prev[1], here
    return None


def reread_findings(script, cwd, depth, trusted, how, outer_danger=False,
                    local=True):
    """Findings for text another reader runs, each labelled with how it got there.

    outer_danger is the reading line's own danger, carried in so the script
    is read with the line's caution: `bash -c 'eval "$X"'` beside an
    `rm -rf` bails as an `eval` on the line itself would. Past MAX_DEPTH a
    line with danger asks, and one with none has nothing left to hide.
    The script is read with reread set, so no variable in it gets a free
    pass; see classify_one. local is analyse's: False for a reader on
    another machine, or one whose $HOME this line does not prove.
    """
    if depth + 1 > MAX_DEPTH:
        return ([bare("ask", f"{how} nested too deeply to analyse")]
                if outer_danger else [])
    return [f._replace(label=f"{how}; {f.label}" if f.label else how)
            for f in analyse(script, cwd, depth + 1, trusted=trusted,
                             outer_danger=outer_danger, reread=True,
                             local=local)]


# A value an env file assigns: plain word characters and path punctuation,
# $NAME and ${NAME} (closed), single-quoted runs, double-quoted runs of the
# same, and a `$(...)` closed on the same line whose body holds no parenthesis,
# backtick, quote other than a plain double-quoted run, backslash, comment,
# heredoc, list operator or brace -- `BASE=$(cat "$R/3/BASE" 2>/dev/null)`.
# Its body is a substitution the analysis reads like any other. Unquoted `=`
# and `~` are left out: zsh expands `=cmd` and `~user` in an assignment, and
# one that fails stops the rest of the file.
_NAME_REF = r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})"
_SOURCED_VALUE = (r"(?:[A-Za-z0-9_./:@%+,-]|" + _NAME_REF
                  + r"|'[^'\n]*'"
                  + r"|\$\((?:[^()`'\"\\\n#<;&|{}$]|" + _NAME_REF
                  + r"|\"(?:[^\"`\\\n$]|" + _NAME_REF + r")*\")*\)"
                  + r"|\"(?:[A-Za-z0-9_./~:@%+,= -]|" + _NAME_REF + r")*\")")
# The lines inline_source reads in place: blank, a comment, `[export ]NAME=VALUE`,
# and `cd VALUE [|| return [N]]`, each with an optional trailing comment.
SOURCED_LINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"(?:export[ \t]+)?[A-Z_][A-Z0-9_]*=" + _SOURCED_VALUE + r"*"
    r"|(?:cd|chdir)[ \t]+" + _SOURCED_VALUE
    + r"+(?:[ \t]*\|\|[ \t]*return(?:[ \t]+[0-9]+)?)?"
    r")?[ \t]*(?:(?<=[ \t])#.*|^[ \t]*#.*)?$")
# The name an env-file line assigns. Upper case only, as env files are written:
# zsh's lower-case specials (status, pipestatus, the zsh/parameter arrays)
# refuse an assignment, and one refused stops the rest of the file.
SOURCED_NAME_RE = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Z_][A-Z0-9_]*)=")
# ...and the upper-case names bash or zsh hold read-only or special, which fail
# the same way: `UID=1000` is `failed to change user ID` in zsh, `PPID=1` and
# `TTYIDLE=1` read-only. The zsh 5.9 and bash 5.3 lists, so a newer shell may
# add one; an unlisted special costs the file being read when it should ask.
SHELL_SPECIAL_NAMES = {
    "ARGC", "BASHOPTS", "BASHPID", "BASH_ARGC", "BASH_ARGV", "BASH_ARGV0",
    "BASH_COMMAND", "BASH_LINENO", "BASH_SOURCE", "BASH_SUBSHELL",
    "BASH_VERSINFO", "BASH_VERSION", "EGID", "EPOCHREALTIME", "EPOCHSECONDS",
    "ERRNO", "EUID", "FUNCNAME", "GID", "GROUPS", "HISTCMD", "LINENO",
    "PIPESTATUS", "PPID", "SHELLOPTS", "SIGNALS", "SRANDOM", "TTYIDLE", "UID",
    "USERNAME", "ZCURSES_COLORS", "ZCURSES_COLOR_PAIRS", "ZFTP_SESSION",
    "ZSH_ARGZERO", "ZSH_EVAL_CONTEXT", "ZSH_EXECUTION_STRING", "ZSH_NAME",
    "ZSH_PATCHLEVEL", "ZSH_SCRIPT", "ZSH_SUBSHELL", "ZSH_VERSION",
}
QUOTED_SPAN_RE = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*"|\\.""", re.S)


def _brackets_balance(text):
    """Do (), {} and [] pair up in text, quoted spans and escapes aside?

    A sourced file left inside one -- `X=${`, `X=$[`, `arr=(a` -- fails as a
    whole, and tokenizes as a plain assignment word.
    """
    pairs, stack = {")": "(", "}": "{", "]": "["}, []
    for c in QUOTED_SPAN_RE.sub("", text):
        if c in "({[":
            stack.append(c)
        elif c in pairs:
            if not stack or stack.pop() != pairs[c]:
                return False
    return not stack


def inline_source(cmd, toks, cwd):
    """`. FILE; rest` leading the line -> FILE's text, a newline, then rest.

    A sourced file is code that runs in this very shell, so the honest reading
    of the line is the file's text in its place. Nothing on the line runs
    before the first command, so nothing on it can have written the file.
    Every refusal is None, and the caller asks exactly as it always did.
    """
    if NO_DISK or len(toks) < 2:
        return None
    m = SOURCE_LEAD_RE.match(cmd)
    if not m or m.group(1) != toks[1]:
        return None
    if len(toks) > 2 and toks[2] not in (";", "&&"):
        return None
    path = m.group(1)
    if "/" not in path:
        return None                   # a bare name is looked up on $PATH first
    # The shells disagree on these, so neither reading is the line's: bash's
    # `.` takes `-/env.sh` as an invalid option and zsh sources it, and zsh
    # reads a leading `=` (or one after `=`, under MAGIC_EQUAL_SUBST) as a
    # command-path expansion that bash leaves literal.
    if path.startswith("-") or "=" in path:
        return None
    if path.startswith("~/"):
        home = _home()
        if not home:
            return None
        path = home + path[1:]
    elif "~" in path:
        return None
    if not posixpath.isabs(path):
        if not (cwd and posixpath.isabs(cwd)):
            return None
        path = posixpath.join(cwd, path)
    # The path as written, never its realpath: the kernel resolves it here as
    # it will for the shell. realpath is lexical about `..` and a trailing
    # `/`, so `env.sh/` and `notadir/../env.sh` read a file that both shells
    # then fail to source with ENOTDIR -- and the rest runs without it.
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > MAX_SOURCED_BYTES:
            return None
        # Non-blocking, and checked again once open: a FIFO swapped in after
        # the stat would hang the open. Capped as well: a /proc file reports
        # size 0, and a file can grow between the calls.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return None
            data = fh.read(MAX_SOURCED_BYTES + 1)
        if len(data) > MAX_SOURCED_BYTES:
            return None
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    # An allow would approve the file's commands too, so RISKY_NEIGHBOURS has to
    # see them; main() only ever sees the line. An alias rewrites what a later
    # word means, which no rule here models.
    if risky_neighbour(text) or re.search(r"(?<![\w-])alias(?![\w-])", text):
        return None
    # The primary gate: every line must be one of the shapes an env file is
    # made of. Each is a complete simple command on its own, so nothing the
    # file leaves open can reach the rest of the line -- the checks after this
    # one were each written for a shape that got past the one before, and are
    # kept as a second wall.
    lines = text.split("\n")
    if not all(SOURCED_LINE_RE.match(line) for line in lines):
        return None
    if any((nm := SOURCED_NAME_RE.match(line)) and nm.group(1) in SHELL_SPECIAL_NAMES
           for line in lines):
        return None
    # bash and zsh parse a sourced file on its own. An unclosed quote fails
    # only the source, and a heredoc left open ends at the end of the file;
    # either way the rest of the line still runs. Joined, the same file pulls
    # the rest into its quote or its heredoc body: `rm -rf /` then reads as
    # unparseable or as data. So the join stands only where it reads, token
    # for token, as the file alone, a separator, then the rest alone.
    rest = cmd[m.end():]
    joined = text + "\n" + rest
    try:
        ftoks = tokenize(text)
        if tokenize(joined) != ftoks + [";"] + tokenize(rest):
            return None
    except ValueError:
        return None
    # Composing token for token is not enough: the file must also be finished.
    # Both shells drop a compound or a list the file leaves open -- `for d in
    # /tmp/y; do` with no done, `R=/tmp/x &&` -- and go on to the rest of the
    # line, so what the file seems to set is never set: with the rest `rm -rf
    # "$d"/*`, that is rm -rf /*. Counting keywords cannot tell (`do :` then
    # `echo done` balances), so any compound or grouping word, or a list
    # operator at the end, keeps the ask.
    if any(t in GROUPING_WORDS or t in SOURCED_OPENERS for t in ftoks):
        return None
    # The same for what the tokens do not show. An unmatched backtick
    # (`R=/tmp/x && X=\``) fails the whole file and survives tokenizing as part
    # of a word, so the masked text is searched for any substitution or
    # arithmetic left open; and an operator other than the four plain list
    # ones -- `;;`, `;&`, `&`, `|&` -- ends the file somewhere the shells refuse.
    masked, _ = mask_opaque(text)
    if re.search(r"`|\$\(|\(\(", masked) or not _brackets_balance(masked):
        return None
    if any(re.match(r"^[;&|()]+$", t) and t not in (";", "&&", "||", "|")
           for t in ftoks):
        return None
    while ftoks and ftoks[-1] == ";":
        ftoks.pop()
    if ftoks and ftoks[-1] in SEPARATORS:
        return None
    # A redirection with no target (`X=1 2>` at the end of the file, or
    # before a separator) is a syntax error that fails the file too.
    for k, t in enumerate(ftoks):
        if (t in REDIRECTS or REDIRECT_TOKEN_RE.match(t)) and (
                k + 1 >= len(ftoks) or ftoks[k + 1] in SEPARATORS):
            return None
    return joined


# --- analysis ----------------------------------------------------------------

# The non-rm commands this file knows how to reason about. Used to find one
# inside a segment when command_word cannot.
HANDLED = {"find", "fd", "fdfind", "rsync", "rclone", "ssh"} | GIT_NAMES


def analyse_segment(argv, cwd, depth, assigned=None, loops=None,
                    may_clear=False, git_relocated=False, trusted=True,
                    opaque_names=frozenset(), line_literals=None,
                    reread=False, outer_danger=False, glued=False,
                    read_xargs=True, home=None, session_cwd=None, condemn_cwds=(),
                    pwd_here=False, rebuilt_names=frozenset()):
    """-> [Finding] for the non-rm commands in one segment.

    may_clear is decided per SEGMENT by analyse, because that is the unit a
    rebinding can sit before or after; every target found in here shares it.
    git_relocated is decided per LINE, for the reason GIT_RELOCATE_RE gives.
    trusted is analyse's: False inside a string another shell re-reads, and
    passed on to every target this judges. opaque_names, rebuilt_names,
    line_literals, reread, glued, home and pwd_here are analyse's too; see
    classify_one. outer_danger is the line's danger, which an ssh remote
    string is read with, as reread_findings reads a script. read_xargs False reads an xargs segment's words as
    written, never through the xargs: one level, so nesting stays linear.
    session_cwd is the session's cwd when cwd, the candidates, no longer
    starts with it, and condemn_cwds the readings that may only condemn; see
    classify_operand's base and condemn_cwds.
    """
    out = []
    segment = argv
    idx, base = command_word(argv)
    # xargs CMD ARGS runs CMD ARGS with its input: judge exactly that. The
    # input stands where xargs puts it, as XARGS_INPUT; see with_xargs_input.
    # The words as written are judged too, as the fallback below reads them
    # (read_xargs=False), and the worse verdict stands: the input may take
    # the place of a literal that reads as a deny on its own -- `xargs -I/
    # find / -delete`, or the `/` that appended input makes rsync's source.
    # xargs options the guard cannot read leave only that reading.
    if base == "xargs" and read_xargs:
        p = parse_xargs(argv, idx)
        if p is not None and p[0] >= len(argv):
            return out           # no command: xargs runs echo
        if p is not None:
            same = (cwd, depth, assigned, loops, may_clear, git_relocated,
                    trusted)
            kw = dict(opaque_names=opaque_names, rebuilt_names=rebuilt_names,
                      line_literals=line_literals,
                      reread=reread, outer_danger=outer_danger, glued=glued,
                      home=home, session_cwd=session_cwd,
                      condemn_cwds=condemn_cwds, pwd_here=pwd_here)
            return (analyse_segment(argv, *same, read_xargs=False, **kw)
                    + analyse_segment(with_xargs_input(argv[p[0]:], p[1]),
                                      *same, **kw))
    # command_word once stopped on a wrapper's own option argument, leaving it
    # standing where the command should be: `sudo -u root find / -delete`
    # stopped at `-u`, `nice -n 5` at `-n`, and `ionice` is no wrapper at all.
    # Every one of those passed in silence. WRAPPER_OPTS reads the options
    # now, and this stays as the fallback for the wrappers it does not list.
    # rm never had this problem because it is scanned across all tokens. A
    # guarded name appearing as a mere argument costs a prompt at worst, and
    # only when a destructive flag sits beside it.
    if base not in HANDLED:
        for j, t in enumerate(argv):
            if command_name(t) in HANDLED:
                idx, base = j, command_name(t)
                break
    argv = argv[idx:]
    if not argv:
        return out

    def rate(targets, label, missing, bare_var_ok=False, follows=None):
        """Judge each target, tagging every Finding with the same why-this-one label.

        follows names a flag that makes the command follow symlinks below
        its targets (find -L, fd --follow). Every root's proof is about the
        root alone, under /tmp and the cwd as under the home roots, so such a
        target asks where it would have cleared. A deny stays a deny.
        """
        if not targets:
            out.append(Finding("ask", segment, label, None, missing))
            return
        for t in targets:
            d, r = classify_operand(t, cwd, bare_var_ok=bare_var_ok,
                                    assigned=assigned, loops=loops,
                                    may_clear=may_clear,
                                    loops_may_clear=trusted,
                                    opaque_names=opaque_names,
                                    rebuilt_names=rebuilt_names,
                                    line_literals=line_literals, reread=reread,
                                    glued=glued, home=home, base=session_cwd,
                                    condemn_cwds=condemn_cwds, pwd_here=pwd_here)
            if follows and d == "allow":
                d, r = "ask", (f"{follows} follows symlinks below {t}, so what it "
                               f"deletes is not bounded by it ({r})")
            out.append(Finding(d, segment, label, t, r))

    def input_runs(flags, what):
        """xargs's input after an exec flag is in the command run per match,
        whose arguments the roots say nothing about."""
        k = next((n for n, t in enumerate(argv) if t in flags), None)
        if k is not None and any(XARGS_INPUT in t for t in argv[k + 1:]):
            out.append(Finding("ask", segment, f"{what} runs a command on each "
                               "match", XARGS_INPUT, "xargs puts its input among "
                               "that command's arguments, which the search "
                               "roots say nothing about"))

    if base == "find":
        destructive, roots, follows = find_targets(argv)
        if destructive:
            rate(roots,
                 "find deletes everything under its search roots",
                 "no search root given, so find descends from the current directory",
                 follows=follows)
        input_runs(FIND_EXEC_ACTIONS, "find")
    elif base in ("fd", "fdfind"):
        destructive, roots, follows = fd_targets(argv)
        if destructive:
            # fd with no path argument searches the current directory, exactly
            # as `find . ` does -- so treat it as the literal `.` rather than as
            # a missing operand.
            rate(roots or ["."],
                 "fd -x/-X runs a destructive command on everything under its search roots",
                 "no search root given, so fd descends from the current directory",
                 follows=follows)
        input_runs(FD_EXEC_FLAGS, base)
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
        elif outer_danger and XARGS_INPUT in sub:
            out.append(Finding("ask", segment, "rclone's subcommand", None,
                               "xargs supplies rclone's subcommand from its "
                               "input, so what it does is not in the command"))
    elif base in GIT_NAMES:
        # No finding at all unless the tree moved -- see the module docstring.
        # `clean` anywhere in the segment rather than git_subcommand's answer,
        # because `git $OPT clean` puts an expansion where it looks. On a line
        # with a delete, a subcommand xargs supplies may be a clean too.
        if git_relocated and ("clean" in argv or (
                outer_danger and XARGS_INPUT in git_subcommand(argv)[1])):
            out.append(Finding("ask", segment, GIT_CLEAN_LABEL, None,
                               GIT_RELOCATED))
    elif base == "ssh":
        # The local shell expands a double-quoted remote command before ssh
        # runs, and shlex has stripped the quotes by now, so `ssh h "rm -rf
        # $W/*"` and `ssh h 'rm -rf $W/*'` are indistinguishable here. So the
        # remote string is analysed untrusted: its own bindings and loops may
        # condemn, never clear. `ssh h "X=/tmp/a; rm -rf $X/*"` cleared as
        # /tmp/a/* until this, and runs as `rm -rf /*` when X is unset here.
        remote = ssh_remote_command(argv)
        # A remote command that starts with an expansion or a substitution is
        # whatever that holds, which the far end reads as code -- `ssh h "$S"`,
        # `ssh h "${S}x"`. Unreadable, as shell_script calls a `-c "$S"`. It
        # is still analysed below, so a deny later in the string wins.
        if outer_danger and remote.lstrip().startswith(("$", "`")):
            out.append(Finding("ask", segment, "over ssh", None,
                               "the remote command starts with an expansion, "
                               "so what runs on the far end cannot be read"))
        # The far end parses its command as shell code, so input xargs puts
        # there is code too -- not an operand, as it is to a local command.
        if outer_danger and XARGS_INPUT in remote:
            out.append(Finding("ask", segment, "over ssh", None,
                               "xargs puts its input into the remote command, "
                               "which the far end runs as shell code"))
        if remote and depth < MAX_DEPTH:
            # No local cwd applies on the far end, so pass cwd="": only /tmp and
            # /var/tmp stay safe and every other absolute path asks. The findings
            # keep the REMOTE segment as their echo -- that is the command that
            # matters -- and carry the hop in their label. Read as any re-read
            # text is: with the line's danger, and no variable free pass.
            hop = "over ssh -- no local safe root clears a path on the far end"
            for f in analyse(remote, "", depth + 1, trusted=False,
                             outer_danger=outer_danger, reread=True,
                             local=False):
                out.append(f._replace(
                    label=f"{hop}; {f.label}" if f.label else hop))
        elif remote:
            out.append(Finding("ask", segment, "over ssh", None,
                               "ssh nested too deeply to analyse"))
        # Base read every word after the host as the remote command, so `ssh
        # h -o 'rm -rf /'` denied, though -o takes that word for its value
        # and no command runs. That reading's denies stand: no deny may
        # weaken, as the old tokenizer's do not.
        as_base = ssh_command_as_base_read(argv)
        if as_base and as_base != remote and depth < MAX_DEPTH:
            hop = "over ssh, as base read its command"
            out.extend(f._replace(label=f"{hop}; {f.label}" if f.label else hop)
                       for f in analyse(as_base, "", depth + 1, trusted=False,
                                        outer_danger=outer_danger, reread=True)
                       if f.decision == "deny")
    return out


def ssh_command_as_base_read(argv):
    """ssh's remote command as base read it: options only before the host,
    and every word after the host the command."""
    idx, _ = command_word(argv)
    i = idx + 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in SSH_VALUE_OPTS and len(argv[i]) == 2 else 1
    rest = argv[i + 1:]
    return rest[0] if len(rest) == 1 else " ".join(rest)


# Analysis at any depth sets this flag when it sees an unread destructive
# heredoc; depth 0 decides. A heredoc whose terminator is followed by the
# closing quote of an ssh remote string only matches HEREDOC_RE inside that
# string, one level down, where a `return []` would read to the caller as
# "nothing found". Only "was anything recorded" is visible, not what.
_WITHDRAWN = [False]
# Analysis at any depth sets this when its text may send a relative cd
# elsewhere (cd_may_search), and every candidate_cwds after it reads it. Each
# text read below a line is part of that line's own text, except a sourced
# file, which is read in its place and tested there.
_CD_SEARCHES = [False]


def _note_unread_heredocs(cmd, read=()):
    """Record that cmd holds a destructive heredoc body nothing read as a
    script. Called before every return that could otherwise lose it."""
    if unread_destructive_heredoc(cmd, read):
        _WITHDRAWN[0] = True


def _quiet_redirects(seg):
    """Does every redirection in seg leave its text unrunnable?

    Input redirections only read, a duplication onto 0, 1, 2 or - only moves a
    stream the line already has, and /dev/null swallows one. Anything else
    writes the text somewhere a later command could run it from -- `>&60`
    reaches a coprocess. `|&` is a pipe to the shell but not a separator to
    segment_bounds, so it is refused here outright.
    """
    j = 0
    while j < len(seg):
        t = seg[j]
        if t == "|&":
            return False
        if REDIRECT_TOKEN_RE.match(t):
            target = seg[j + 1] if j + 1 < len(seg) else ""
            op = t.lstrip("0123456789")
            if op.startswith("<") and ">" not in op:
                j += 2
                continue
            if op.endswith("&") and target in ("0", "1", "2", "-"):
                j += 2
                continue
            if target == "/dev/null":
                j += 2
                continue
            return False
        j += 1
    return True


def _inert_line(toks, bounds, text):
    """Is this whole line a plain read-and-print command list?

    The backstop asks "did a real recursive rm fail to parse?". It may answer
    no only for a line that is an allowlist end to end: every command on it is
    a reader, a safe sink, cd/ls/pwd/true or a bare assignment, and every
    `rm -rf` the pattern finds sits in an argument of a reader. It is judged
    on the line, not on the hit's segment, because a pipe or redirect on a
    group (`{ echo '...'; } | sh`) sits outside any one segment. Each clause
    shuts a shape that ran the text although the per-token rule passed it:

    1. Every match is a hit -- `grep -n 'rm -rf' f; =rm -rf d` has a match
       (zsh's =rm is /usr/bin/rm) that no hit token holds.
    2. Each hit is an argument of a bare-named reader, with no substitution --
       `./grep` is a path to some other binary and `grep() { eval ..; }` a
       function, so the command word must be exactly the name.
    3. Every segment is an allowed command or pure assignments -- a wrapper,
       env prefix (`LESSOPEN='|-sh %s' less`), shell or path makes it False.
       sort (`-o`, `--compress-program`), uniq (`- file`) and less are absent.
    4. No grouping, compound or function word, and no `<(`, `>(`, `>>(` or
       `()` token -- zsh's `echo '...' >>(sh)` pipes into a shell.
    5. Every redirection is quiet: input, /dev/null, or a duplication onto
       0, 1, 2 or - (not `>&60`, a coprocess).
    6. No executing option: `rg --pre=sh` and `--hostname-bin` run a program,
       `printf -v c '...'; $c` stores the text for the shell to run.
    """
    hits = [i for i, t in enumerate(toks) if RM_RECURSIVE_RE.search(t)]
    if not hits:
        return False
    if (len(RM_RECURSIVE_RE.findall(text))
            > sum(len(RM_RECURSIVE_RE.findall(toks[i])) for i in hits)):
        return False
    for t in toks:
        if t in GROUPING_WORDS:
            return False
        if PUNCT_ONLY_RE.match(t) and ("(" in t or ")" in t):
            return False
    for s, e in bounds:
        seg = toks[s:e]
        first = seg[0]
        if all(ENV_ASSIGN_RE.match(t) and not has_subst(t) for t in seg):
            continue
        if "/" in first or first not in INERT_COMMANDS:
            return False
        if not _quiet_redirects(seg):
            return False
        if first == "rg" and any(
                t in ("--pre", "--hostname-bin")
                or t.startswith(("--pre=", "--hostname-bin=")) for t in seg):
            return False
        if first == "printf" and any(t.startswith("-v") for t in seg):
            return False
    for i in hits:
        k = next((n for n, (s, e) in enumerate(bounds) if s <= i < e), None)
        if k is None:
            return False
        s = bounds[k][0]
        if (toks[s] not in INERT_READERS or "/" in toks[s] or i == s
                or has_subst(toks[i])):
            return False
    return True


def analyse(cmd, cwd, depth=0, trusted=True, outer_danger=False, reread=False,
            local=True):
    """-> [Finding]. Empty means the guard has no opinion.

    trusted is False for a string another shell re-reads -- an ssh remote
    command, a `sh -c` script. Its double quotes are gone by the time it gets
    here, so a `$X` in it may have been expanded by the OUTER shell, and
    nothing bound inside it may clear. Bindings and loops still condemn.

    outer_danger is True for a script re-read from a line that is itself
    dangerous; see reread_findings. Such a script gets the line's bail-outs
    even when its own text names nothing destructive.

    reread is True for any text another reader runs, a quoted heredoc fed to
    a shell included, and switches off the variable free passes; see
    classify_one. Off by default: a line analysed as itself keeps them.

    local is False on a far end (ssh, a shell inside another command), where
    $HOME and ~ are not this machine's and no home-relative rule applies --
    and for any text another reader runs when the reading line does not
    itself prove $HOME, since that reader inherits the line's.
    """
    # The allow is withdrawn, as RISKY_NEIGHBOURS does in main(): an allow
    # would also approve a destructive heredoc body that something on the line
    # may run. Silence leaves the line to normal permissions. Never touches an
    # ask or a deny. Decided here, once, over what every depth recorded.
    if depth == 0:
        _WITHDRAWN[0] = False
        _CD_SEARCHES[0] = False
    findings = _analyse(cmd, cwd, depth, trusted, outer_danger, reread, local)
    if (depth == 0 and _WITHDRAWN[0] and findings
            and all(f.decision == "allow" for f in findings)):
        return []
    return findings


def expansion_runs_recursive(cmd):
    """Does a segment run a command named only by an expansion, with a
    recursion-shaped flag beside it?

    danger_hint reads names, and `${X:-rm} -rf /` names none it can match:
    the rm sits after a `-`, where RM_WORD_RE will not start. Whatever the
    expansion holds runs with -rf. Tokenized only when the raw text has both
    a `$` and such a flag, so most lines never pay for it. The command an
    xargs runs counts: `ls | xargs $X -rf` is the same hole.
    """
    if "$" not in cmd or not RECURSIVE_FLAG_RE.search(cmd):
        return False
    try:
        toks = tokenize(cmd)
    except ValueError:
        return False
    for s, e in segment_bounds(toks):
        j = effective_command(toks[s:e])[0]
        if (s + j < e and EXPANSION_WORD_RE.match(toks[s + j])
                and any(RECURSIVE_FLAG_RE.search(t) for t in toks[s + j + 1:e])):
            return True
    return False


# A substitution body that only prints a heredoc, after strip_data: the
# commit-message idiom `-m "$(cat <<'EOF' ... EOF)"`, whose body is prose.
CAT_HEREDOC_RE = re.compile(r"^\s*cat\s+HEREDOC\s*$")


# What a message substitution's code may not hold, past its heredoc bodies: an
# expansion as a command word, at the start or after a separator; `.` sourcing
# a file; or a word that runs text it is handed -- eval, source, xargs, a shell.
MESSAGE_HIDES_RE = re.compile(
    r"(?:^|[;&|\n(){}!])\s*[\"']*\$"
    r"|(?:^|[;&|\n(){}!])\s*\.(?=\s)"
    r"|(?<![\w.-])(?:eval|source|xargs|%s)(?![\w-])"
    % "|".join(sorted(SHELLS)))


def _message_runs_destructive(body):
    """Does a substitution skipped as message text still read as destructive?

    The value of `-m "$(...)"` is data to every raw-text rule, but the
    substitution in it runs. Only its heredoc bodies are prose, and only when
    it just cats them; a shell or an ssh reading one runs it. Everything else
    is code, nested message flags included -- `-m "$(echo -m "$(rm -rf /)")"`
    -- so it withdraws on danger, on an expansion run with a recursive flag,
    on a nested substitution, and on anything that hides what it runs (see
    MESSAGE_HIDES_RE, and the command words found by tokenizing). A message
    whose substitution runs a `$VAR` loses its allow; that is the cost.
    """
    code = strip_heredocs(body)
    if (danger_hint(code) or expansion_runs_recursive(code) or has_subst(code)
            or MESSAGE_HIDES_RE.search(code)):
        return True
    # The same, past a wrapper or an assignment: `A=1 $X`, `exec $X`.
    try:
        toks = tokenize(code)
    except ValueError:
        return True
    for s, e in segment_bounds(toks):
        j, base, _, known = command_word_detail(toks[s:e])
        if s + j < e and (not known or "$" in toks[s + j]
                          or base in OPAQUE or base in SHELLS):
            return True
    return (not CAT_HEREDOC_RE.match(strip_data(body))
            and any(danger_hint(b) for b in heredoc_bodies(body).values()))


def _analyse(cmd, cwd, depth, trusted, outer_danger=False, reread=False,
             local=True):
    """_analyse_line, and the line as written still condemns.

    A command word read as its proven literal clears what the expansion
    asked for, but it may also hide a deny the line as written gives: once
    `B=git; $B rm -rf /` reads as `git rm`, the rm is git's argument and no
    rm scan reaches it. So when a word was resolved and nothing in that
    reading denies, the line is read again with its words as written, and
    each deny found there stands. That reading may not withdraw an allow or
    mark the line as searching CDPATH: only its denies count.

    A word that may run either of two literals -- `w && B=a || B=b; $B` --
    is read once per choice, and every reading's findings stand.

    A word left unproven though the line assigns its name a literal somewhere
    -- after a cd that may fail, or quoted, `"B=rm"` -- may hold that
    literal too: the line is read with each in its place, and only the denies
    of that reading count, beside the ask the unproven word keeps.
    """
    resolved = []
    findings = _analyse_line(cmd, cwd, depth, trusted, outer_danger, reread,
                             local, resolved)
    for picks in _other_picks(resolved):
        findings = findings + _analyse_line(cmd, cwd, depth, trusted, outer_danger,
                                            reread, local, [], picks)
    if not any(f.decision == "deny" for f in findings):
        state = _WITHDRAWN[0], _CD_SEARCHES[0]
        extra = []
        if any(isinstance(got, dict) for got in resolved):
            extra += _analyse_line(cmd, cwd, depth, trusted, outer_danger, reread,
                                   local, None)
        if LOOSE in resolved:
            loose = []
            extra += _analyse_line(cmd, cwd, depth, trusted, outer_danger, reread,
                                   local, loose, None, True)
            for picks in _other_picks(loose):
                extra += _analyse_line(cmd, cwd, depth, trusted, outer_danger,
                                       reread, local, [], picks, True)
        _WITHDRAWN[0], _CD_SEARCHES[0] = state
        findings = findings + [f for f in extra if f.decision == "deny"]
    return findings


# Marks a reading that left a command word unproven though the line assigns
# its name a literal; see _analyse.
LOOSE = "loose"


def _other_picks(resolved):
    """-> each choice of candidates past the first a reading resolved: one
    {index: choice} per combination of the words that may run more than one
    literal. resolve_command_words keeps their product within MAX_CWDS."""
    alts = {i: c for got in resolved if isinstance(got, dict)
            for i, c in got.items() if len(c) > 1}
    keys = list(alts)
    for n in range(1, _product(len(c) for c in alts.values())):
        picks, k = {}, n
        for i in keys:
            picks[i], k = k % len(alts[i]), k // len(alts[i])
        yield picks



def _product(sizes):
    n = 1
    for s in sizes:
        n *= s
    return n


def _analyse_line(cmd, cwd, depth, trusted, outer_danger=False, reread=False,
                  local=True, resolved=None, picks=None, loose=False):
    # resolved: a list, to read each proven command word as its literal and
    # note each word's candidates; None reads every word as written. picks
    # chooses among a word's candidates. See _analyse.
    #
    # A command named only by an expansion and handed a recursive flag hides
    # whatever it runs, and names nothing guarded for the scans below to find.
    # The token check sees this line's own segments; EXPANSION_FLAG_RE sees one
    # in text a reader below runs, which must get past the early exits too.
    runs_hidden = (expansion_runs_recursive(cmd)
                   or bool(EXPANSION_FLAG_RE.search(cmd)))

    # Raw scan first: no guarded command name anywhere means nothing to judge.
    # Not for a script re-read from a dangerous line: an `eval "$X"` or a `$X`
    # run as a command names nothing guarded, and is what the bail-outs below
    # exist to catch. The heredoc record still happens, at the end.
    if not outer_danger and not runs_hidden and not GUARDED_CMD_RE.search(cmd):
        _note_unread_heredocs(cmd)
        return []

    # Blank out `git rm`, then look again. If nothing guarded survives, the
    # command is git's business and none of ours. Done on the raw text so the
    # heuristics below never see a git rm either.
    text = GIT_RM_RE.sub(" git-rm ", cmd)
    # ...and blank the parts of the line that are data rather than code, so a
    # commit message quoting `git clean -fdx` is not read as running it.
    text = strip_data(text)
    if (not outer_danger and not runs_hidden and not GUARDED_CMD_RE.search(text)
            and not heredoc_bodies(cmd)):
        _note_unread_heredocs(cmd)
        return []

    # The bail-outs below exist to stop a *destructive* command hiding inside
    # something unreadable. With no destructive pairing anywhere -- no recursion
    # flag next to an rm, no -delete next to a find -- there is nothing to hide,
    # so `ls | xargs rm` goes straight to the token scan instead of prompting.
    # A re-read script inherits its line's danger: `rm -rf ./b; bash -c 'eval
    # "$X"'` hides as much as the same eval written on the line itself.
    danger = danger_hint(text) or outer_danger or runs_hidden
    if danger and _heredoc_skipped(cmd):
        _note_unread_heredocs(cmd)
        return [bare("ask", "a heredoc here could not be placed -- its opening line "
                            "continues, or a comment comes before it -- so what is "
                            "its body and what is code cannot be told apart")]

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
            _note_unread_heredocs(cmd)
            return []
        return [bare("ask", f"command could not be parsed ({e})")]

    bounds = segment_bounds(toks)

    # Consulted to escalate from anywhere, and to clear only before rebind_at;
    # see classify_one.
    assigned = proven_bindings(cmd)
    # The hook's own $HOME is a fact about this machine, so it enters the map
    # as a binding would -- condemning from anywhere, clearing only behind
    # may_clear -- unless the line could change it: by writing the name, or
    # by reaching something that rebinds without one.
    home = _home() if local else None
    if home and _read_only_mentions("HOME", cmd) and not _indirection_present(cmd):
        assigned = dict(assigned, HOME=home)
    # The free passes read a name off a token shlex has unquoted, which is
    # not the name the shell expands once a quote glues a word on.
    # A heredoc body is not this line's code -- one fed to a shell is read
    # on its own -- so its text does not count.
    glued = bool(QUOTE_GLUE_RE.search(strip_heredocs(cmd)))
    # Where the map stops being provable. Computed once over the whole token
    # stream rather than per segment, because the answer is a property of the
    # LINE -- the first construct that could rebind a name without writing it --
    # and asking each segment separately would only rediscover it len(bounds)
    # times. A segment starting before it ran with the bindings still intact.
    rebind = _rebinding_possible(toks, cmd)
    rebind_at = len(toks) if rebind is None else rebind[0]
    if not trusted:
        rebind_at = 0
    # A command word that is a plain $B whose binding holds there is B's
    # literal, and every rule below reads the segment as if it were typed:
    # `B=/x/bin/tool; "$B" args` runs that tool, not something unknowable.
    # Not on a glued line, where the name read may not be the one expanded.
    # A name the line rewrites without naming it proves nothing past the
    # leading run, and a quoted word may run by its name alone.
    if not glued and resolved is not None:
        masked0 = mask_opaque(cmd)[0]
        read = _quoted_and_starts(toks, masked0)
        words, rebind_at, alts, hint = resolve_command_words(
            toks, bounds, assigned, home, rebind_at, cmd,
            refused=frozenset(n for _, n, _ in unread_sets(toks)),
            quoted=read[0] if read else frozenset(), picks=picks,
            unassigned=quoted_assignments(toks, masked0), loose=loose)
        if hint:
            resolved.append(LOOSE)
        if words is not toks:
            resolved.append(alts)
            toks = words

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

        Naming the construct is not enough by itself: `eval` says which word
        stopped the guard and nothing at all about what eval was told to run. So
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

    # Asks that do not end the analysis, so a deny elsewhere on the line still
    # wins: `$X; rm -rf /` must stay a deny. Added to the findings below.
    unreadable = []

    # Each segment's command as effective_command reads it, past wrappers and
    # through xargs. Where an xargs runs one: (the index that command starts
    # at, its segment's end, the -I string or None) -- the stretch of tokens
    # that xargs's input reaches.
    effective = {start: effective_command(toks[start:end])
                 for start, end in bounds}
    xargs_regions = [(start + at, end, rep) for start, end in bounds
                     for at, rep in effective[start][4] if start + at < end]

    # Opaque constructs bail -- but only in *command position*. `.` is the POSIX
    # source builtin at the start of a segment and a jq filter in `jq -r . f`;
    # matching it positionally is the difference between the two. Position is
    # effective_command's, so a wrapper, a keyword or a group in front of the
    # word (`sudo $(x) -rf /`, `( eval "$X" )`) does not hide it, and an xargs
    # in front of it is read through: `xargs eval` runs eval. Each one adds an
    # ask and the analysis goes on, so a deny elsewhere on the line still wins.
    if danger:
        for start, end in bounds:
            j, base, _, known, runs = effective[start]
            i = start + j
            # An -I string is replaced inside every word xargs runs, so one in
            # the command or a wrapper in front of it lets the input choose
            # what runs, and one in an option word what that option says.
            # Elsewhere it makes an operand the input's, judged as such below.
            for at, rep in runs:
                if rep is None:
                    continue
                for k in range(start + at, end):
                    if rep in toks[k] and (k <= i or toks[k].startswith("-")):
                        what = ("part of the command it runs, so which "
                                "command runs" if k <= i else
                                "an option of the command it runs, so what "
                                "that option does")
                        unreadable.extend(bail(
                            "ask", k, f"xargs puts its input in place of "
                                      f"{mark(rep)} in {mark(toks[k])}, "
                                      f"{what} is not in the command", hi=k))
            if i >= end:
                # No command after an xargs that appends its input to words
                # it was given: `xargs sudo`, `xargs timeout 5` and `xargs
                # xargs` all run that input. A bare `xargs` runs echo.
                at = next((start + a for a, rep in runs
                           if rep is None and start + a < end), None)
                if at is not None:
                    unreadable.extend(bail(
                        "ask", at, f"{mark('xargs')} appends its input where "
                                   "the command should be, so the command "
                                   "that runs is that input", hi=at))
                continue
            word = toks[i]
            if not known and base == "xargs":
                unreadable.extend(bail(
                    "ask", i, f"{mark('xargs')} has options the guard cannot "
                              "read, so what it runs cannot be found"))
            elif not known:
                # `env -Z $X`, `env -S "$X"`: an option the guard cannot read
                # may take the next word as its value, or be the command.
                # timeout's duration `$T` may split and move the command.
                unreadable.extend(bail(
                    "ask", i, f"{mark(word)} is an option or argument of the "
                              "wrapper in front of it that the guard cannot "
                              "read, so which command runs is unknowable"))
            elif XARGS_INPUT in word:
                # Only in text read again, where xargs's input already stands
                # in: `ls | xargs ssh h` runs the input on the far end.
                unreadable.extend(bail(
                    "ask", i, f"{mark(word)} runs in command position, and "
                              "xargs supplies it from its input"))
            elif has_subst(word):
                # A substitution there hides which command runs, so nothing
                # the operand checks below can prove says anything about it.
                unreadable.extend(bail(
                    "ask", i, f"{mark(word)} runs in command position, so "
                              "which command runs is unknowable"))
            elif base in OPAQUE:
                # Fix 3: `. FILE` or `source FILE` as the line's first word
                # reads FILE in its place, and the line is judged as that text
                # followed by the rest. Only for a line analysed as itself:
                # text another reader runs -- a `bash -c` script, a heredoc
                # fed to a shell, an ssh string -- has no cwd or $HOME the
                # guard can vouch for, and keeps this ask. Every path out of
                # the recursive analysis records the inlined text's heredocs,
                # which hold all of this line's: `. FILE;` cannot open one.
                if (i == 0 and base in (".", "source") and depth < MAX_DEPTH
                        and trusted and not reread):
                    inlined = inline_source(cmd, toks, cwd)
                    if inlined is not None:
                        how = f"with {toks[1]} read in place"
                        return [f._replace(label=f"{how}; {f.label}"
                                           if f.label else how)
                                for f in analyse(inlined, cwd, depth + 1,
                                                 trusted=trusted,
                                                 outer_danger=outer_danger,
                                                 reread=reread, local=local)]
                name = f"xargs {base}" if runs else base
                unreadable.extend(bail(
                    "ask", i, f"{mark(name)} hides its arguments -- what it "
                              "runs on is not in the command"))
            elif EXPANSION_WORD_RE.match(word) or EXPANSION_RE.search(base):
                # A command name built from an expansion is the same hole:
                # `X="rm -rf /usr"; $X` runs what X holds, and `${X}rm -rf /*`
                # names no rm until X is known. `$X/bin/rm` is still rm.
                unreadable.extend(bail(
                    "ask", i, f"{mark(word)} runs in command position, so "
                              "which command runs is unknowable"))
            elif computed_target(toks[start:end], j) is not None:
                # `N=W; declare "$N"=/usr` sets W with no W in the text, so
                # nothing below can say W was set.
                target = computed_target(toks[start:end], j)
                unreadable.extend(bail(
                    "ask", i, f"{mark(base)} assigns to a name it computes "
                              f"({mark(target)}), so which variable it sets "
                              "is not in the command"))
    # A script handed to a shell is code the guard can read. Each one found
    # here is analysed after the line's own findings, below. "here" is whether
    # the shell IS its segment's command: then the script runs in this shell's
    # cwd. As anyone else's argument -- docker exec, find -exec, kubectl --
    # it may run in another filesystem, and is judged as the ssh far end is.
    scripts = []
    for i, t in enumerate(toks):
        base = command_name(t)
        if base not in SHELLS:
            continue
        si = shell_script(toks, i)
        if si is None:
            continue
        if si is False:
            if danger:
                why = ("runs fish, not POSIX shell, so its script is not read"
                       if base == "fish" else
                       "has options the guard cannot read, or a script held in "
                       "an expansion, so what it runs cannot be found")
                unreadable.extend(bail("ask", i, f"{mark(base + ' -c')} {why}"))
            continue
        s, e = next((s, e) for s, e in bounds if s <= i < e)
        here = s + command_word(toks[s:e])[0] == i
        scripts.append((i, si, here))
        # xargs writes its input into the script in place of its -I string,
        # and the shell parses the result: the input is code there, not an
        # operand. The script is still read, so a deny in it wins.
        reps = {rep for rs, re_, rep in xargs_regions
                if rep is not None and rs <= si < re_ and rep in toks[si]}
        if danger and reps:
            unreadable.extend(bail(
                "ask", i, f"{mark(base + ' -c')} runs a script xargs writes its "
                          f"input into, in place of {mark(min(reps))}, so the "
                          "script is not in the command", hi=si))

    if "--no-preserve-root" in toks:
        # The flag itself is the danger, so it is highlighted along with what
        # follows it rather than left to read as one more red flag among -rf.
        i = toks.index("--no-preserve-root")
        return bail("deny", i,
                    f"{mark('--no-preserve-root')} defeats rm's own safety net",
                    hi=i)

    # Every name the line assigns that the binding map could not prove. One
    # with any value the guard cannot read -- a substitution, a positional, an
    # expansion -- is opaque. Every literal it is assigned, after an `echo` or
    # a `cd X &&` that ends the leading run, is judged, and may condemn; see
    # classify_one. An opaque value itself leaves the ${W:?} contract alone.
    line_values = {}
    for k, t in enumerate(toks):
        if ENV_ASSIGN_RE.match(t):
            name, value = t.split("=", 1)
            if name not in assigned:
                line_values.setdefault(name, []).append((k, value))
    opaque_names = frozenset(n for n, vs in line_values.items()
                             if not all(LITERAL_VALUE_RE.match(v) for _, v in vs))
    # ...and the names it sets with no assignment token to read: `printf -v W
    # /` is W=/ to the shell. Their literals stay, and may still condemn --
    # except one such a builtin has certainly replaced by the time a segment
    # runs: `W=; printf -v W x; rm -rf "$W"/*` runs as rm -rf x/*, and the
    # empty literal no longer says anything. A literal assigned after the
    # set, or one the set may not reach, still condemns. Such a name is
    # rebuilt where the text shows it, so the free passes do not cover it;
    # see classify_one.
    unread = unread_sets(toks)
    rebuilt_names = frozenset(n for _, n, _ in unread)
    opaque_names |= rebuilt_names
    overwrites = [(k, n) for k, n, by_builtin in unread
                  if by_builtin and _overwrites_before(toks, bounds, k)]

    def literals_at(pos):
        """{name: values} the literals the line assigns, as they stand for the
        segment starting at pos -- a value read_value reads included, as the
        paths it may hold. A name with other values too keeps its literals:
        `W=/; f || W=$(g); rm -rf "${W:?}"/x` may run with W=/."""
        out = {}
        for n, vs in line_values.items():
            lits = []
            for k, v in vs:
                if any(n == on and k < ok < pos for ok, on in overwrites):
                    continue
                if LITERAL_VALUE_RE.match(v):
                    lits.append(v)
                else:
                    lits.extend(value_reads.get(k, ((), ""))[0])
            if lits or all(LITERAL_VALUE_RE.match(v) for _, v in vs):
                out[n] = list(dict.fromkeys(lits))
        return out

    # $PWD is where the shell stands, which the cwd readings follow, unless
    # the line writes PWD -- `PWD=/`, `export PWD`, `read PWD` -- or a reader
    # may have expanded it elsewhere: the outer shell, for a `-c` script or
    # a substitution's body, or another machine's, on a far end. A segment
    # from rebind_at on may follow something that set it without naming it.
    pwd_ok = local and not reread and not glued and _read_only_mentions("PWD", cmd)
    # These two do clear as well as condemn, and both earn it the same way: a
    # loop's word list and a literal `cd` target are complete and visible right
    # here, where an assignment map's binding may already have been overwritten.
    # A word built from bindings earns it only where they still hold: before
    # rebind_at, and on a line where no quote glues a name to the next word.
    def resolve(w, at):
        """A loop word or cd target, through bindings that hold at token at."""
        return _resolve_word(w, assigned if at < rebind_at and not glued else None,
                             home)
    loops = loop_bindings(toks, resolve)
    # An IFS the line writes splits every word an unquoted $C holds, and
    # rebind_at does not gate a loop's words; see SplitWords.
    if not _read_only_mentions("IFS", cmd):
        loops = {n: (ws if ws is None else SplitWords(ws)) for n, ws in loops.items()}
    masked = mask_opaque(cmd)[0]
    scopes = loop_scopes(toks, masked)
    # A line that may look a cd target up elsewhere says so for every text
    # read below it too: `zsh -T -c '...'` sets the flag the script obeys.
    if cd_may_search(cmd, toks):
        _CD_SEARCHES[0] = True
    marks = []
    readings = cwd_readings(toks, cwd, resolve, _CD_SEARCHES[0], masked, marks)
    cwds = list(dict.fromkeys(readings))
    repeats = loop_bodies(toks, masked)
    spans, functions = loop_spans(toks, masked), defines_function(toks)
    quoted_starts = _quoted_and_starts(toks, masked)

    def cwds_at(seg_start):
        """-> (the cwds a command at seg_start may run in, condemn-only ones).

        Two readings are dropped, both the user's decision: those of a cd
        that comes after the command, unless a loop or a function may run
        it again; and, for a command that runs only if a cd succeeded, the
        session's own cwd. That cwd still builds the safe roots (base,
        below). Every dropped reading may still condemn, so no deny the
        line-wide reading gave is lost.
        """
        k = next(n for n, (s, _) in enumerate(bounds) if s == seg_start)
        here = readings
        # Any word of the segment, not its first: `repeat 2 do rm -rf x`
        # opens its loop inside the segment, and `f() { rm` its body.
        if not any(t in repeats for t in range(*bounds[k])):
            here = readings[:marks[k + 1]] + readings[marks[-1]:]
        if cd_must_have_run(toks, bounds, k, quoted_starts):
            here = here[1:]
        here = list(dict.fromkeys(here)) or cwds
        return here, [c for c in cwds if c not in here]

    budget = [READ_BUDGET]
    refused = frozenset(n for _, n, _ in unread)
    unassigned = quoted_assignments(toks, masked)

    def read_value(value, k, depth=0, stack=frozenset()):
        """-> (paths, state) for the value assigned at token k; stack holds
        the assignments being read, so one that reads itself is seen.

        state "read": the parser can read it, and paths are every value it
        may hold -- through `~` and `~user`, $HOME's binding, $PWD where the
        assignment runs, a name proven, a loop's words, and every value the
        line assigns a name before k (or anywhere, where k may run again: a
        loop's words are not all `x=..` in its body leaves), and a default
        or alternate word read the same way: ${N:-word},
        ${N-word}, ${N:=word}, ${N::=word}, ${N:+word}, ${N:?}. "unknown": it
        cannot, from a substitution, a positional or a name from outside the
        line, and the ${W:?} contract holds. A name's line values are read
        but leave it unknown too, unless one certainly ran before k
        (line_values_at): `false && X=sub` may leave X as it was, so
        `${X:-$HOME}` reads its word. "held": the free pass is
        refused, and it asks. A held value names a critical directory the
        guard cannot place ($TMPDIR, $OLDPWD, $HOME or $PWD where they are
        not proven), or one beside an unknown (`${X:-$HOME}`). It may hold
        a word that cannot be read naming one, or one that starts with `/`.
        It may have dots beside an unknown (`..$X`, `$X/.`), or be the root
        once its unknowns are empty (`/$X`). It may read a variable through
        another's value (zsh's ${(P)N}, bash's ${!N}), read a name the line
        sets some way the scan cannot read (`printf -v`, `+=`, `${N:=x}`), or
        one that may already hold this very value (`x=$x/..` in a loop), or be
        a chain too long or too wide to follow (MAX_DEPTH, READ_BUDGET), or
        hold more than MAX_CWDS paths.
        Paths come back for a held value too, so what it may hold still
        condemns: `D=${X:-$HOME}` may be $HOME.
        """
        if depth == 0:
            budget[0] = READ_BUDGET
        budget[0] -= 1
        if depth > MAX_DEPTH or budget[0] < 0:
            return [], "held"
        at = segment_start(k)
        text, critical, held = value, False, False
        if text.startswith("~"):
            head, sep, rest = text.partition("/")
            user, root = head[1:], None
            if not user and at < rebind_at:
                root = assigned.get("HOME")
            elif user and local and BARE_NAME_RE.match(user):
                try:
                    root = pwd.getpwnam(user).pw_dir
                except (KeyError, OSError):
                    root = None
            if not root:
                return [], "held"
            text, critical = root + sep + rest, True
        if ":~" in text:
            return [], "held"
        refs = value_refs(text)
        if refs is None:
            return [], "held"
        opaque = has_subst(text) or SENTINEL in text
        unknown, parts = opaque, []
        here_loops = loops_at(loops, scopes, at)
        for s, e, kind, name, op, word in refs:
            if kind == "indirect":
                return [], "held"
            if kind == "other":
                if name in DANGEROUS_VARS:
                    return [], "held"
                unknown = True
                parts.append((s, e, [], True))
                continue
            vals, maybe = [], False
            # What else may have set name by the time k runs: an assignment
            # before k, or anywhere when k may run again -- in a loop's body,
            # or after a function definition. Inside the body of a loop that
            # binds name, the loop overwrites it each time round, so what
            # comes after k counts only where a loop inside this one, or a
            # function, may run it first. What came before the loop is
            # overwritten too, but still read, as it always was: one more
            # value only condemns.
            opener = max((a for a, b in scopes.get(name, ()) if a < at < b),
                         default=None) if name in here_loops else None

            def reaches(kk):
                if opener is None:
                    return kk < k or k in repeats
                return kk < k or functions or any(
                    opener < o < k < c and kk < c for o, c in spans)

            sets = [(kk, vv) for kk, vv in line_values.get(name, ()) if reaches(kk)]
            # A set the scan cannot read -- printf -v, an append, ${W:=x},
            # a reference -- refuses the pass wherever it may reach k.
            if any(n in (name, ANY_NAME) and reaches(kk) for kk, n, _ in unread):
                held = True

            def read_sets():
                nonlocal held, maybe
                for kk, vv in sets:
                    if LITERAL_VALUE_RE.match(vv):
                        vals.append(vv)
                    elif kk in stack:
                        held = True    # `x=$x/..` where it runs again
                    else:
                        got, st = read_value(vv, kk, depth + 1, stack | {kk})
                        held = held or st == "held"
                        maybe = maybe or st == "unknown"
                        vals.extend(got)

            if name in here_loops:
                words = here_loops[name]
                if words is None:
                    return [], "held"
                maybe = isinstance(words, OutsideLoop)
                for w in words:
                    if LITERAL_VALUE_RE.match(w):
                        vals.append(w)
                        continue
                    got, st = read_value(w, k, depth + 1, stack)
                    held = held or st == "held"
                    maybe = maybe or st == "unknown"
                    vals.extend(got)
                # Its words are not all it may hold: the body, or a function
                # it calls, may assign it before k reads it.
                read_sets()
            elif name == "PWD":
                if not (pwd_ok and at < rebind_at):
                    return [], "held"
                vals = cwds_at(at)[0]
                if not all(c and c != UNKNOWN_CWD and posixpath.isabs(c) for c in vals):
                    return [], "held"
                critical = True
            elif name in assigned and (name != "HOME" or at < rebind_at):
                vals = [assigned[name]]
                critical = critical or name in DANGEROUS_VARS
            elif name in DANGEROUS_VARS:
                return [], "held"
            elif sets:
                # Values name may hold, not the one it does: the line may
                # not run them -- `false && X=sub`, an `if` arm, a pipeline
                # stage, a later pass of a loop -- and it keeps its own.
                # Only an assignment certain to have run before k says so.
                read_sets()
                if line_values_at(toks, bounds, k, name, refused, unassigned) is None:
                    maybe = True
            else:
                maybe = True
            if op is not None:
                # The word, read as the value is. One that cannot be read
                # refuses the pass when it starts with `/`; a critical name,
                # `~` or `..` in it refuse it from inside the read.
                wvals, wst = (([""], "read") if word == "" else
                              read_value(word, k, depth + 1, stack))
                held = held or wst == "held" or (wst == "unknown"
                                                 and word.startswith("/"))
                if op == "::=":
                    vals, maybe = wvals, wst == "unknown"
                elif op in (":-", ":=", "-", "="):
                    # The word stands in when N is unset -- or empty, with
                    # the colon. An unknown N may be either.
                    empty = op in (":-", ":=")
                    if maybe or (empty and "" in vals):
                        vals = [v for v in vals if v or not empty] + wvals
                        maybe = maybe or wst == "unknown"
                elif op in (":+", "+"):
                    # The word when N is set -- and not empty, with the
                    # colon -- and nothing otherwise.
                    nonempty = op == ":+"
                    is_set = maybe or any(v or not nonempty for v in vals)
                    unsets = maybe or any(not v and nonempty for v in vals) or not vals
                    vals = (wvals if is_set else []) + ([""] if unsets else [])
                    maybe = (maybe and wst == "unknown") or (is_set and wst == "unknown")
                elif op == ":?":
                    vals = [v for v in vals if v]
            unknown = unknown or maybe
            parts.append((s, e, list(dict.fromkeys(vals)), maybe))
        # Every path it may hold that the parser can spell out. An unknown
        # value keeps those too, for a line literal only condemns: `D=${X:-/}`
        # may be /.
        paths = [] if opaque or any(not vals for _, _, vals, _ in parts) else [""]
        last = 0
        for s, e, vals, _ in parts:
            # More than MAX_CWDS of them refuse the pass, as item 4's words
            # do: every path is read, or none clears. The first MAX_CWDS
            # still condemn.
            if len(paths) * len(vals) > MAX_CWDS:
                held = True
            paths = [p + text[last:s] + v for p in paths for v in vals][:MAX_CWDS]
            last = e
        paths = [p + text[last:] for p in paths]
        if unknown and critical:
            held = True
        # Dots beside an unknown: `..$X`, `$X..`, `$X/..`, `$X/.`.
        if unknown and (".." in text or any(
                maybe and text[e:e + 2] == "/." and text[e + 2:e + 3] in ("", "/")
                for _, e, _, maybe in parts)):
            held = True
        # ...and one that is the root once its unknowns are empty: `D=/$X`
        # is / when X is, and ${D:?} cannot see that.
        if unknown and not opaque:
            bare = text
            for s, e, vals, maybe in reversed(parts):
                bare = bare[:s] + ("" if maybe or not vals else vals[0]) + bare[e:]
            if bare.startswith("/") and not bare.strip("/"):
                held = True
        return paths, "held" if held else "unknown" if unknown else "read"

    # Every value the line assigns that is no plain literal, read where it
    # can be. A held one refuses its name the free passes.
    value_reads = {k: read_value(v, k, 0, frozenset({k})) for vs in line_values.values()
                   for k, v in vs if not LITERAL_VALUE_RE.match(v)}
    rebuilt_names = rebuilt_names | {n for n, vs in line_values.items()
                                     if any(value_reads.get(k, ((), ""))[1] == "held"
                                            for k, _ in vs)}

    in_git = git_segments(toks)
    findings = list(unreadable)

    # --- rm itself. Scanned across all tokens, not just command position, so
    # `sudo rm -rf` and `xargs rm -r` are both seen.
    found_rm = False
    i = 0
    while i < len(toks):
        if command_name(toks[i]) not in RM_NAMES or in_git[i]:
            i += 1
            continue
        found_rm = True
        segment = segment_at(i)
        seg_start = segment_start(i)
        may_clear = seg_start < rebind_at
        lits = literals_at(seg_start)
        rm_at = i
        i += 1
        recursive, operands, flags, maybe_flag = False, [], [], False
        while i < len(toks) and toks[i] not in SEPARATORS:
            t = toks[i]
            if t in REDIRECTS:
                i += 2  # skip redirect target
                if operands and operands[-1].isdigit():
                    operands.pop()  # that was an fd, not an operand
                continue
            # A flag may be held in a variable: `C=-rf; rm $C /` is rm -rf /.
            # A proven one is read as written there, and a literal the line
            # assigns makes the rm recursive when it is a recursive flag. Any
            # other word that is only expansions may be one; see below.
            if EXPANSION_WORD_RE.match(t):
                val = (_resolve_word(t, assigned, home)
                       if may_clear and NAME_WORD_RE.match(t) else None)
                if val is not None and val.startswith("-") and len(val) > 1:
                    t = val
                elif val is None:
                    name = t.strip("${}")
                    # A loop's words too: `for a in -rf; do rm $a /usr/x`.
                    seen = (list(lits.get(name, ())) + [assigned.get(name) or ""]
                            + list(loops.get(name) or ()))
                    if any(v.startswith("-") and not v.startswith("--")
                           and any(c in v for c in "rR") for v in seen):
                        recursive = True
                    maybe_flag = True
                    # Unquoted, a value with whitespace splits into words (in
                    # bash, and zsh under SH_WORD_SPLIT): `Y='-rf /'; rm $Y`
                    # is rm -rf /. Its flags count, and the words after them
                    # are operands as written.
                    if not quoted_starts or i not in quoted_starts[0]:
                        for v in seen:
                            if not re.search(r"\s", v):
                                continue
                            for w in v.split():
                                if w == "--recursive" or (
                                        w.startswith("-") and not w.startswith("--")
                                        and any(c in w for c in "rR")):
                                    recursive = True
                                elif not w.startswith("-"):
                                    operands.append(w)
            if t.startswith("--"):
                flags.append(t)
                if t in ("--recursive", "--dir"):
                    recursive = True
            elif t.startswith("-") and len(t) > 1:
                flags.append(t)
                if any(c in t for c in "rR"):
                    recursive = True
            else:
                operands.append(t)
            i += 1
        # An rm anywhere in what an xargs runs gets that xargs's input as
        # operands the command never shows: appended, or wherever its -I
        # string sits -- in `xargs -I{} find . -exec rm -rf {} \;` too, where
        # find's {} is xargs's first. An -I string inside a flag makes the
        # flag the input's, so recursion is assumed, as for a substitution.
        # The operands as written stay too, and the worst verdict wins: the
        # input may displace a literal that is a deny on its own, as in
        # `xargs -I/ rm -rf /`.
        # Whatever xargs hands rm comes from a producer the guard cannot
        # bound -- `find ~ -print0 | xargs -0 rm -f --` deletes every file
        # under $HOME -- so it asks, recursive or not. A `--` before the input
        # stops it smuggling in flags (`echo -rf / | xargs rm` is rm -rf /),
        # not paths.
        for rs, re_, rep in xargs_regions:
            if rs <= rm_at < re_:
                args = toks[rm_at + 1:i]
                at = (len(args) if rep is None else
                      next((n for n, t in enumerate(args) if rep in t), None))
                # An -I string rm's arguments never hold puts no input there.
                if at is not None:
                    findings.append(Finding(
                        "ask", segment, None, None,
                        f"{mark('xargs')} hands {mark(toks[rm_at])} paths from its "
                        "input, which the guard cannot bound"))
                    break
        for rs, re_, rep in xargs_regions:
            if rs <= rm_at < re_:
                recursive = recursive or (rep is not None
                                          and any(rep in f for f in flags))
                operands = list(dict.fromkeys(
                    operands + with_xargs_input(operands, rep)))
        # A literal argument list settles whether this rm recurses. A substitution
        # in it does not: `rm $(echo -rf) /tmp/x` carries no -r the scanner can
        # see, and recursion is exactly what makes rm dangerous, so assume it.
        # Nor does a word that may be a flag held in a variable: `rm $C /`.
        # That reading keeps only a deny, and judges only the operands such a
        # flag would apply to, so `rm "$f"` and `rm -f "$t" "$t.bak"` stay
        # silent: asking there too cost 30 real cleanup lines.
        condemn_only = False
        if not recursive and not any(has_subst(o) for o in operands):
            if not maybe_flag:
                continue
            condemn_only = True
            operands = [o for o in operands if not EXPANSION_WORD_RE.match(o)]
        if not operands:
            continue
        here, gone = cwds_at(seg_start)
        # docker, podman or kubectl running the rm inside a container: it
        # starts in whatever directory the image sets, which nothing here
        # places, as a far end's is. The host cwd is read too, and the worse
        # verdict stands: base read `.` there and denied it at $HOME, and
        # `docker run -v ~:/h -w /h img rm -rf .` does delete $HOME.
        if _in_container(toks[seg_start:rm_at]):
            here = ([here] if isinstance(here, str) else list(here)) + [""]
        for op in operands:
            d, r = classify_operand(op, here, assigned=assigned,
                                    loops=loops_at(loops, scopes, seg_start),
                                    may_clear=may_clear, loops_may_clear=trusted,
                                    opaque_names=opaque_names,
                                    rebuilt_names=rebuilt_names,
                                    line_literals=lits, reread=reread,
                                    glued=glued, home=home, base=cwd,
                                    condemn_cwds=gone,
                                    pwd_here=pwd_ok and may_clear)
            if condemn_only and d != "deny":
                continue
            if condemn_only:
                r = (f"an expansion in this rm's arguments may hold a recursive "
                     f"flag; {r}")
            # No label: for rm every operand is a target, which the echoed
            # command already makes obvious.
            findings.append(Finding(d, segment, None, op, r))

    # rmdir, unlink, shred and the rest under xargs delete whatever its
    # input names, as rm does there, and nothing else reads them.
    for rs, re_, rep in xargs_regions:
        name = effective_command(toks[rs:re_])[1]
        if name in DESTRUCTIVE_EXEC and name not in RM_NAMES and (
                rep is None or any(rep in t for t in toks[rs + 1:re_])):
            findings.append(Finding(
                "ask", segment_at(rs), None, None,
                f"{mark('xargs')} hands {mark(name)} paths from its input, which "
                "the guard cannot bound"))

    # --- everything else, one segment at a time.
    git_relocated = git_relocates(text, toks, bounds)
    for start, end in bounds:
        findings.extend(
            analyse_segment(toks[start:end], cwds_at(start)[0], depth, assigned,
                            loops_at(loops, scopes, start),
                            start < rebind_at, git_relocated, trusted,
                            opaque_names=opaque_names, rebuilt_names=rebuilt_names,
                            line_literals=literals_at(start), reread=reread,
                            outer_danger=danger, glued=glued, home=home,
                            session_cwd=cwd, condemn_cwds=cwds_at(start)[1],
                            pwd_here=pwd_ok and start < rebind_at))

    # Scripts collected above, and quoted heredocs a shell reads from stdin.
    # A -c script is untrusted: its double quotes may have let the OUTER shell
    # expand it first. A quoted heredoc body is literal -- nothing expanded it.
    here_cwd = cwds[0] if len(cwds) == 1 else UNKNOWN_CWD

    def home_holds_at(pos):
        """Does a reader started at token pos inherit the $HOME this line
        proves? Text another reader runs sees the line's HOME, written or
        not, so its own text alone cannot prove it."""
        return "HOME" in assigned and pos < rebind_at
    for i, si, here in scripts:
        findings.extend(reread_findings(
            toks[si], here_cwd if here else UNKNOWN_CWD, depth, False,
            f"{command_name(toks[i])} -c re-reads this", danger,
            local=here and home_holds_at(segment_start(i))))
    bodies = heredoc_bodies(cmd)
    read_bodies = set()
    if bodies:
        for k in range(len(bounds)):
            got = shell_stdin_script(toks, bounds, k, bodies)
            if got is None:
                continue
            ph, here = got
            read_bodies.add(ph)
            # A shell behind another command is read as the ssh far end is:
            # no local cwd, and nothing bound in the body clears.
            findings.extend(reread_findings(
                bodies[ph], here_cwd if here else UNKNOWN_CWD, depth, here,
                "a shell reads this heredoc" if here else
                "a shell behind another command reads this heredoc -- no "
                "local cwd applies", danger,
                local=here and home_holds_at(bounds[k][0])))
        # A known shell fed a heredoc that was not read above -- fish, a
        # script file's stdin, a `-c` beside it, the redirection written first
        # -- may still run the body. A destructive one asks rather than
        # leaving the line to the withdrawal's silence. The shell may be the
        # command word or further along: `ssh h fish`.
        for k, (s, e) in enumerate(bounds):
            words = [n for n in range(s, e) if toks[n] not in bodies]
            j, base = command_word([toks[n] for n in words])
            if base not in SHELLS:
                j = next((n for n in range(j + 1, len(words))
                          if command_name(toks[words[n]]) in SHELLS), len(words))
                base = command_name(toks[words[j]]) if j < len(words) else ""
            if base not in SHELLS or j >= len(words):
                continue
            fed = [t for t in toks[s:e] if t in bodies]
            if k > 0 and toks[bounds[k - 1][1]] == "|":
                fed += [t for t in toks[bounds[k - 1][0]:bounds[k - 1][1]]
                        if t in bodies]
            if any(ph not in read_bodies and danger_hint(bodies[ph]) for ph in fed):
                findings.extend(bail(
                    "ask", words[j], f"{mark(base)} reads a heredoc whose body "
                                     "is destructive, and the guard cannot read "
                                     "it as that shell's script"))
    # A command substitution's body is a script the shell runs, so it is read
    # as one. Untrusted: inside double quotes the outer shell's own expansions
    # reach it first. Three things are undone first, so the body reads as the
    # shell will run it:
    #   - Heredocs were masked before the substitution was found, so its body
    #     holds their placeholders, which the next level strips as stray
    #     sentinels. Each goes back as written: `x=$(bash <<'EOF' ...)`.
    #   - A backtick body is unescaped as bash does it, `\\`, `` \` `` and `\$`
    #     losing their backslash, which is how backticks nest.
    #   - `$((...))` is arithmetic only when its inner `(` closes at the end;
    #     otherwise bash and zsh run it as a substitution of a subshell. Real
    #     arithmetic runs nothing but the substitutions inside it, so it is read
    #     as the arguments of `:` -- a `$N` there is not a command word.
    # A substitution inside a message-flag value is not read: strip_data treats
    # that value as data. It still runs, so one that reads as destructive
    # withdraws the allow; see _message_runs_destructive.
    masked, subs = mask_opaque(cmd)
    clean = cmd.replace(SENTINEL, "").replace(MARK, "")
    heredocs = [(ph + _heredoc_opener_rest(m), m.group(0))
                for ph, m in _masked_heredocs(clean)]
    msgs = [m.span() for m in MSG_FLAG_RE.finditer(masked)]
    for ph, original in subs.items():
        at = masked.find(ph)
        in_message = any(s <= at < e for s, e in msgs)
        if original.startswith("`"):
            body = original[1:-1]
        elif (original.startswith("$((")
              and _close_paren(original, 2) == len(original) - 2):
            body = ": " + original[3:-2]
        else:
            body = original[2:-1]
        for held, written in heredocs:
            body = body.replace(held, written)
        if original.startswith("`"):
            body = BACKTICK_ESCAPE_RE.sub(r"\1", body)
        if in_message:
            if _message_runs_destructive(body):
                _WITHDRAWN[0] = True
            continue
        findings.extend(reread_findings(
            body, here_cwd, depth, False, "a command substitution runs this", danger,
            local="HOME" in assigned))

    # A relocated clean the segments never saw: inside a substitution that is
    # not in command position (`echo $(git --work-tree=/ clean -fdx)`), whose
    # body mask_opaque hid from the tokens, or with its subcommand spelled by
    # one (`git --work-tree=/ $(echo clean)`). The raw text still shows it.
    if (git_relocated and GIT_CLEAN_RE.search(text)
            and not any(f.reason == GIT_RELOCATED for f in findings)):
        findings.append(bare("ask", f"{mark('git clean')} on a line that "
                                    f"relocates its work tree -- {GIT_RELOCATED}"))

    _note_unread_heredocs(cmd, read_bodies)

    if not found_rm and not findings:
        # Nothing survived parsing. Of the constructs that could execute a
        # string, eval bailed above, xargs's command was read as the command it
        # is, and a <shell> -c script, a heredoc a shell reads and a
        # substitution's body were each re-read as a script, so a bare
        # mention here is inert text -- unless it actually reads as a
        # recursive rm, in which case we failed to parse something real and
        # must not approve it. Searched against the git-stripped text, so an
        # exempted `git -C /path rm -r` does not read as unparsed.
        m = RM_RECURSIVE_RE.search(text)
        if m:
            # The whole line is plain readers and every match is their text:
            # there is no rm here to have failed to parse.
            if _inert_line(toks, bounds, text):
                return []
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
    global NO_DISK
    NO_DISK = "--no-disk" in sys.argv[1:]
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
