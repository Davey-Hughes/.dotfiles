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
from rm_guard import MARK  # noqa: E402

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
]


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

    for name, cmd, expected, got, reason in fails:
        first = reason.replace("\x1b", "\\e").splitlines()[0][:160] if reason else ""
        print(f"FAIL {name}")
        if cmd:
            print(f"  cmd:  {cmd!r}")
        print(f"  want: {expected}   got: {got}")
        if first:
            print(f"  why:  {first}")
        print()

    total = len(CASES) + 5 + RENDER_CHECKS
    print(f"{total - len(fails)}/{total} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
