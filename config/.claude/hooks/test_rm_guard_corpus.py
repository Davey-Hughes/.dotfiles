#!/usr/bin/env python3
"""Verdicts rm_guard.py must produce for REAL commands, not invented ones.

    python3 test_rm_guard_corpus.py              # the committed fixture (CI)
    python3 test_rm_guard_corpus.py --live       # ...plus today's transcripts
    python3 test_rm_guard_corpus.py --regenerate # rewrite the fixture

test_rm_guard.py asserts what the guard should do. This asserts what it DID do
to commands that actually ran on this machine, and the two catch different
things. Every regression in the binding-clearing work was caught here and by
nothing else:

    a loop rule that read a `for` printing results AFTER a delete as a reason
    to distrust the delete                                  cost 4 real allows
    a backstop that called `[ -z "$f" ]` an unidentifiable
    command word                                            cost 2 real allows
    a builtin matched in an echo's prose, from the title
    "### EXPERIMENT A: ... (no trap backup)"                cost 1 real allow

None of those would have been hand-written, because nobody knew the shapes
existed. That is the whole argument for a corpus: it is not cleverer than the
suite, it is just not imaginary.

--- why the fixture is anonymised, and why that is safe -------------------

The source is ~/.config/.claude/projects/**/*.jsonl -- live session
transcripts, which .gitignore and tests/test-tracked-files.sh exist to keep
OUT of this repo. Committing them verbatim would defeat the one check whose
failure cannot be undone by a later commit.

So the fixture is rewritten before it lands: every path component under $HOME
and every session slug is mapped to n<i>, the username and org name are
removed wherever they appear, private-key filenames become example_*, URLs and
addresses become example.test, and heredoc bodies and -m message strings --
the one unbounded prose surface -- are replaced with filler.

The rewrite is verdict-preserving BY CONSTRUCTION, not by hope. --regenerate
computes each verdict from the original command, then again from the rewritten
one with $HOME remapped to match, and refuses to write the fixture unless all
of them agree. A rewrite that changed what the guard sees would abort rather
than quietly test something that never happened. That check earned itself
immediately: an early mapper collected a directory literally named `home`,
substituted it everywhere, turned /home/u into /n70/u and changed the path
DEPTH -- which is an input to classify_path. One verdict moved, and that is
how the bug was found.

What the rewrite does NOT promise is that no private string survives. It is a
denylist over free text, and a `pgrep -f "release/foo[b]ar"` -- the standard
trick to stop pgrep matching its own command line -- already hid one project
name by splitting it mid-token. The residual after scrubbing is the shape of
the work (tool names, directory layout), not credentials. gitleaks runs over
the tree in CI for the credential half; see .forgejo/workflows/ci.yml.

--- the three modes -------------------------------------------------------

The fixture is a SNAPSHOT. It cannot grow on its own, and a corpus that never
grows stops finding new shapes -- which is exactly what it is for. So:

    default      replay the fixture. Hermetic, no transcripts needed, runs in
                 CI, and fails if a verdict moved.
    --live       replay the fixture AND rebuild from today's transcripts,
                 reporting commands whose verdict drifted and commands whose
                 shape is not in the fixture at all. This is the mode worth
                 running after touching the guard.
    --regenerate rewrite the fixture from today's transcripts, refusing to
                 write if the anonymisation is not verdict-preserving.

$HOME and $TMPDIR are pinned for every run. safe_roots() folds both in, so a
fixture recorded under one $HOME and replayed under another is not testing the
same thing -- and CI's $HOME is not this machine's.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "rm_guard.py")
FIXTURE = os.path.join(HERE, "rm_guard_corpus.jsonl")
TRANSCRIPTS = os.path.expanduser("~/.config/.claude/projects")

# The cwds each command is judged under. More than one because safe_roots folds
# cwd in, so the same command is a different question depending on where it
# runs -- and the project-dir case only clears when cwd IS that directory.
# These are anonymised names, so they carry nothing and need no rewriting.
FAKE_HOME = "/home/u"
CWDS = [f"{FAKE_HOME}/proj", FAKE_HOME, "/tmp"]

# Regions of a command the shell treats as DATA. rm_guard masks both before it
# reasons about anything, so filler here cannot move a verdict -- and it is the
# only part of a command that can contain arbitrary prose.
HEREDOC_RE = re.compile(
    r"<<(-)?[ \t]*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2.*?^(?(1)\t*)\3$", re.S | re.M)
MSG_FLAG_RE = re.compile(
    r"(?<![\w-])(?:-m|--message|--body|--title|--description|--notes|--subject)"
    r"(?:[ \t]+|=)(?:\$'(?:\\.|[^'\\])*'|'[^']*'|\"(?:\\.|[^\"\\])*\")")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RECURSIVE_RM_RE = re.compile(
    r"(?<![\w-])rm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)")
# Path components that are structure, not identity. Mapping one of these would
# change what the path MEANS: `home` became n70 once, and /home/u turned into
# /n70/u -- a different depth, which classify_path reads.
STRUCTURAL = {"home", "u", "tmp", "usr", "var", "etc", "opt", "srv", "mnt",
              "bin", "lib", "dev", "proc", "run", "config", "cache", "local",
              "share", "state", "ssh", "git", "claude", "projects", "gemini"}


def run_guard(cmd, cwd, home):
    """-> the guard's decision, with the environment pinned."""
    env = dict(os.environ, HOME=home)
    env.pop("TMPDIR", None)
    payload = json.dumps({"tool_name": "Bash", "cwd": cwd,
                          "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, GUARD], input=payload,
                       capture_output=True, text=True, env=env)
    if p.returncode != 0:
        return "CRASH"
    if not p.stdout.strip():
        return "silent"
    try:
        return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError):
        return "UNPARSEABLE"


# --- reading the transcripts -------------------------------------------------

def transcript_commands():
    """-> every distinct Bash command string in the session transcripts.

    Walks the JSON rather than grepping it: a command is a `tool_input.command`
    on a Bash tool_use block, and it can sit at any depth in a message.
    """
    out = []
    for root, _, files in os.walk(TRANSCRIPTS):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8",
                          errors="replace") as fh:
                    for line in fh:
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        _collect(obj, out)
            except OSError:
                continue
    return list(dict.fromkeys(out))


def _collect(node, out):
    if isinstance(node, dict):
        if node.get("name") == "Bash" and isinstance(node.get("input"), dict):
            cmd = node["input"].get("command")
            if isinstance(cmd, str):
                out.append(cmd)
        for v in node.values():
            _collect(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, out)


# --- the rewrite -------------------------------------------------------------

def private_names(texts, home):
    """-> every identifier that names this machine, its owner, or their work.

    Collected from ground truth -- real path components and session slugs --
    rather than from a list somebody remembered to update. The org name is the
    exception: it lives in the git identity and in no path, which is how
    `beaver_ed25519` and `@beaver/...` survived three earlier passes.
    """
    names = set()
    for t in texts:
        t = t.replace(home, FAKE_HOME)
        for m in re.finditer(re.escape(FAKE_HOME) + r"((?:/[A-Za-z0-9_.@-]+)+)", t):
            names.update(p for p in m.group(1).split("/") if p)
        for m in re.finditer(r"/tmp/claude-\d+/-([A-Za-z0-9_-]+)", t):
            names.update(m.group(1).split("-"))
    for key in ("user.email", "user.name"):
        val = subprocess.run(["git", "config", key], cwd=HERE,
                             capture_output=True, text=True).stdout.strip()
        names.update(re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", val))
    names = {n for n in names if len(n) > 3 and n.lower() not in STRUCTURAL}
    # `beaverhealth` in an address also means `beaver` in a key name.
    for n in list(names):
        m = re.match(r"^([a-z]{4,}?)(?:health|corp|labs|inc|io)$", n.lower())
        if m:
            names.add(m.group(1))
    return names


def make_rewriter(texts, home):
    """-> a function rewriting one command, and the name map it uses."""
    mapping = {n: "n%d" % i
               for i, n in enumerate(sorted(private_names(texts, home)))}

    def rewrite(text):
        text = HEREDOC_RE.sub("<<'EOF'\nFILLER\nEOF", text)
        text = MSG_FLAG_RE.sub(lambda m: m.group(0).split(None, 1)[0] + " 'MSG'",
                               text)
        text = UUID_RE.sub("11111111-2222-3333-4444-555555555555", text)
        text = text.replace(home, FAKE_HOME)
        # `pgrep -f "release/foo[b]ar"` splits a name mid-token so pgrep will
        # not match its own command line. Harmless, and it hid a project name
        # from the substitution below until this ran first.
        text = re.sub(r"\[(\w)\]", r"\1", text)
        for real, fake in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
            text = re.sub(r"(?i)(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(real),
                          fake, text)
        # any private-key-shaped filename, whatever it happens to be called
        text = re.sub(r"(?<![\w-])[\w-]*_(ed25519|rsa|ecdsa|dsa)(?![\w-])",
                      r"example_\1", text)
        text = re.sub(r"https?://[\w.-]+", "https://example.test", text)
        text = re.sub(r"\b[\w.-]+@[\w.-]+\.[\w.-]+\b", "u@example.test", text)
        return text

    return rewrite, mapping


# --- the three modes ---------------------------------------------------------

def load_fixture():
    if not os.path.exists(FIXTURE):
        return None
    with open(FIXTURE, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()
                and not l.startswith("#")]


def replay(rows):
    """-> number of failures, printing each."""
    bad = 0
    for row in rows:
        for cwd, want in row["verdicts"].items():
            got = run_guard(row["cmd"], cwd, FAKE_HOME)
            if got != want:
                bad += 1
                print("FAIL cwd=%s  want %s, got %s" % (cwd, want, got))
                print("  " + row["cmd"].replace("\n", "\n  ")[:400])
    return bad


def regenerate():
    if not os.path.isdir(TRANSCRIPTS):
        print("no transcripts at %s -- nothing to regenerate from" % TRANSCRIPTS)
        return 1
    home = os.path.expanduser("~")
    cmds = [c for c in transcript_commands() if RECURSIVE_RM_RE.search(c)]
    print("%d recursive-rm commands in the transcripts" % len(cmds))
    rewrite, mapping = make_rewriter(cmds, home)
    print("%d private identifiers mapped" % len(mapping))

    # The gate. Judge the original under the real environment, judge the
    # rewrite under the fake one, and refuse to write unless every pair agrees.
    real_cwds = [os.path.join(home, "proj"), home, "/tmp"]
    rows, drift = [], 0
    for cmd in cmds:
        anon = rewrite(cmd)
        verdicts = {}
        for real_cwd, fake_cwd in zip(real_cwds, CWDS):
            before = run_guard(cmd, real_cwd, home)
            after = run_guard(anon, fake_cwd, FAKE_HOME)
            if before != after:
                drift += 1
                print("REWRITE CHANGED A VERDICT (%s -> %s) at cwd=%s"
                      % (before, after, real_cwd))
                print("  " + cmd.splitlines()[0][:120])
            verdicts[fake_cwd] = after
        rows.append({"cmd": anon, "verdicts": verdicts})
    if drift:
        print("\nrefusing to write: %d verdicts moved under anonymisation. "
              "The fixture would test something that never happened." % drift)
        return 1
    leaked = leak_check([r["cmd"] for r in rows], home)
    if leaked:
        print("\nrefusing to write: the rewrite left %d private string(s):"
              % len(leaked))
        for s in leaked[:10]:
            print("    %s" % s)
        return 1
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        fh.write("# Generated by test_rm_guard_corpus.py --regenerate.\n"
                 "# Real commands from this machine's session transcripts,\n"
                 "# anonymised under a verdict-preserving rewrite. Do not edit\n"
                 "# by hand: regenerate, so the gate runs.\n")
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    print("\nwrote %d commands x %d cwds to %s"
          % (len(rows), len(CWDS), os.path.basename(FIXTURE)))
    return 0


def leak_check(texts, home):
    """-> private strings still present after the rewrite.

    A denylist, and known to be one. It exists to catch the mechanical misses
    -- a name in a position the mapper did not look at -- not to prove the
    text is clean. gitleaks covers the credential half in CI.
    """
    blob = "\n".join(texts)
    user = os.path.basename(home)
    pats = [re.escape(user), re.escape(home)]
    for key in ("user.email", "user.name"):
        val = subprocess.run(["git", "config", key], cwd=HERE,
                             capture_output=True, text=True).stdout.strip()
        pats += [re.escape(w) for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", val)]
    pats.append(r"[\w-]*_(?:ed25519|rsa|ecdsa|dsa)\b(?<!example_ed25519)")
    found = []
    for p in pats:
        for m in re.finditer("(?i)" + p, blob):
            if m.group(0) not in found:
                found.append(m.group(0))
    return found


def live():
    """Replay the fixture, then look for drift and new shapes in today's data."""
    rows = load_fixture()
    if rows is None:
        print("no fixture -- run --regenerate first")
        return 1
    bad = replay(rows)
    if not os.path.isdir(TRANSCRIPTS):
        print("no transcripts at %s -- fixture-only run" % TRANSCRIPTS)
        return 1 if bad else 0
    home = os.path.expanduser("~")
    cmds = [c for c in transcript_commands() if RECURSIVE_RM_RE.search(c)]
    rewrite, _ = make_rewriter(cmds, home)
    known = {r["cmd"] for r in rows}
    fresh = [c for c in cmds if rewrite(c) not in known]
    print("\nlive: %d recursive-rm commands on disk, %d in the fixture"
          % (len(cmds), len(rows)))
    if fresh:
        print("%d command(s) not in the fixture -- regenerate to cover them:"
              % len(fresh))
        for c in fresh[:10]:
            print("    %s" % c.splitlines()[0][:100])
    else:
        print("every command on disk is covered")
    return 1 if bad else 0


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--regenerate":
        return regenerate()
    if arg == "--live":
        return live()
    rows = load_fixture()
    if rows is None:
        print("no fixture at %s" % FIXTURE)
        return 1
    bad = replay(rows)
    total = sum(len(r["verdicts"]) for r in rows)
    print("%d/%d corpus verdicts hold (%d commands)"
          % (total - bad, total, len(rows)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
