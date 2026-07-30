# rm guard assignment tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `rm_guard.py` clear a destructive operand whose variable the command itself assigns to a literal, so `W=/tmp/build; rm -rf "$W"/*` returns `allow` instead of `ask`.

**Architecture:** A new pure function reads the *raw command text* for the leading run of lone `NAME=VALUE` statements, filters the result through two disqualifiers, and hands the surviving map to `classify_operand`, which substitutes and re-classifies. Raw text rather than the token stream is forced, not stylistic: `shlex` consumes newlines as whitespace, so the sequential form and the env-prefix form tokenize identically and only the first proves anything.

**Tech Stack:** Python 3 stdlib only (`re`, `shlex`, `posixpath`, `os`, `json`). No third-party packages, no test framework — `test_rm_guard.py` is a plain script.

## Global Constraints

- **Never execute any command string from this plan or from the test tables.** Every dangerous string here is *data*: `test_rm_guard.py` serialises it into a JSON payload and writes it to the guard's stdin, and the guard only classifies text. A `rm -rf /` in `CASES` is safe precisely because nothing runs it. Do not paste these strings into a shell to "check" them.
- Full paths: guard is `config/.claude/hooks/rm_guard.py`, tests are `config/.claude/hooks/test_rm_guard.py`. `~/.config/.claude` is a folded symlink to `config/.claude`, so editing the repo file edits the live hook — no install step, and a broken guard affects this session immediately.
- Run the suite with `python3 config/.claude/hooks/test_rm_guard.py` from the repo root. It prints `N/M passed` and exits non-zero on any failure.
- Baseline before you start: **73/73 passed** (`len(CASES)`=51 + wrapper 5 + `RENDER_CHECKS`=17). Confirm this before Task 1 and treat any drop as a regression you caused.
- Existing style is load-bearing: every non-obvious rule in this file carries a comment explaining *why*, in prose, referencing the failure it prevents. Match it. Do not add comments that restate the code.
- Do not add attribution to commit messages (no `Co-Authored-By`, no `Generated with`).
- `git status` must be clean at the end of each task's commit.

---

### Task 1: `proven_bindings` — read what the command proves about its own variables

**Files:**
- Modify: `config/.claude/hooks/rm_guard.py` — insert a new section after the command-substitution section (after `has_subst`, before `# --- segmentation ---` at line 606)
- Modify: `config/.claude/hooks/test_rm_guard.py` — add `BINDING_CASES` + `check_bindings()`, wire into `main()`

**Interfaces:**
- Consumes: `mask_substitutions(cmd) -> (masked, subs)` and `SENTINEL` (both already in `rm_guard.py`); `DANGEROUS_VARS` (line 145)
- Produces:
  - `leading_bindings(cmd) -> ({name: literal}, rest_text)`
  - `proven_bindings(cmd) -> {name: literal}` — the only entry point Task 2 uses
  - `_statement_end(s, i) -> (end, sep)`, `_lone_assignment(stmt) -> (name, value)`, `_read_only_mentions(name, rest) -> bool`
  - `ASSIGN_RE`, `BINDING_SEPS`, `SUBST_NAME_RE` (module constants; `SUBST_NAME_RE` is unused until Task 2)

- [ ] **Step 1: Write the failing test**

In `config/.claude/hooks/test_rm_guard.py`, extend the import at line 29:

```python
from rm_guard import MARK, proven_bindings  # noqa: E402
```

Add this block immediately after the `CASES` list ends (after line 161, before `def check_wrapper():`):

```python
# (command, the bindings it proves)
#
# Unit-level rather than driven through the CLI, because a binding changes no
# verdict on its own -- it changes what classify_operand is able to resolve, and
# pinning the map directly is what localises a failure to the scanner instead of
# to the classifier downstream of it.
BINDING_CASES = [
    # --- proven --------------------------------------------------------------
    ('W=/tmp/build; rm -rf "$W"/*', {"W": "/tmp/build"}),
    # The case that forces raw-text scanning: shlex eats the newline, so this
    # tokenizes exactly like the env-prefix form two rows below.
    ('W=/tmp/build\nrm -rf "$W"/*', {"W": "/tmp/build"}),
    ('W=/tmp/build && rm -rf "$W"/*', {"W": "/tmp/build"}),
    ('A=/tmp; B=x; rm -rf "$A/$B"/*', {"A": "/tmp", "B": "x"}),
    ('W="/tmp/my build"; rm -rf "$W"/*', {"W": "/tmp/my build"}),
    # Last wins, exactly as the shell does.
    ('W=/tmp/x; W=/; rm -rf "$W"/*', {"W": "/"}),
    # A plain read does not disqualify. This is the permissive edge of the
    # other-mention rule and it is deliberate: the alternative is deciding which
    # commands are read-only, which is the reasoning this guard refuses to do.
    ('W=/tmp/x; echo "$W"; rm -rf "$W"/*', {"W": "/tmp/x"}),

    # --- not proven ----------------------------------------------------------
    # An env prefix is expanded from the OUTER scope before the assignment
    # applies, so it proves nothing at all.
    ('W=/tmp/x rm -rf "$W"/*', {}),
    ('export W=/tmp/x; rm -rf "$W"/*', {}),
    # The run must start at offset 0: proving `echo hi` cannot touch W means
    # reasoning about arbitrary commands.
    ('echo hi; W=/tmp/x; rm -rf "$W"/*', {}),
    # A pipeline segment runs in a subshell, so the assignment never escapes.
    ('W=/tmp/x | cat; rm -rf "$W"/*', {}),
    # ...and `||` runs what follows only if the assignment FAILED.
    ('W=/tmp/x || rm -rf "$W"/*', {}),
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
    # A heredoc body cannot begin the run, so it can never manufacture a
    # binding. This is the hole that forced the leading-run design.
    ("cat <<'EOF' > /tmp/f\nW=/tmp/safe\nEOF\nrm -rf \"$W\"/*", {}),
]


def check_bindings():
    """proven_bindings() in isolation. Nothing here is executed -- see module doc."""
    fails = []
    for cmd, want in BINDING_CASES:
        got = proven_bindings(cmd)
        if got != want:
            fails.append(("bindings", cmd, want, got, ""))
    return fails
```

In `main()`, add the call after line 291 and fold the count into `total`:

```python
    fails += check_render()
    fails += check_bindings()
```

```python
    total = len(CASES) + 5 + RENDER_CHECKS + len(BINDING_CASES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: a traceback, not a test failure — `ImportError: cannot import name 'proven_bindings' from 'rm_guard'`. That is the correct failure for this step; the function does not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `config/.claude/hooks/rm_guard.py`, insert this section after `has_subst` (line 603) and before the `# --- segmentation ---` banner (line 606):

```python
# --- bindings the command proves about itself --------------------------------
# `rm -rf "$W"/*` is unprovable on its own and rightly asks. It stops being
# unprovable when the same command string assigns W a literal first:
#
#     W=/tmp/build; rm -rf "$W"/*
#
# Read from the RAW TEXT, which is forced rather than stylistic. shlex consumes
# newlines as whitespace, so these two produce an identical token list:
#
#     W=/tmp/build\nrm -rf "$W"/*    sequential -- the binding holds
#     W=/tmp/build rm -rf "$W"/*     env prefix -- it does NOT, because the
#                                    shell expands $W from the OUTER scope
#                                    before the assignment takes effect
#
# The first proves the operand and the second proves nothing. "\n" is in
# SEPARATORS but no token is ever equal to it, so a token-level scan cannot tell
# these apart and would have to refuse both -- and the newline form is the one
# that actually gets written.
#
# Only the LEADING run of lone assignments counts. That is a narrow rule that
# buys three properties a general dataflow pass would each have to earn:
#
#   unconditional   nothing has run before them, so no `if` branch, no loop and
#                   no subshell can have skipped them or contained them
#   not data        a heredoc body can never BEGIN the run, so the hole where
#                   `cat <<'EOF' ... W=/tmp/safe ... EOF` manufactures a binding
#                   for an unset variable never opens
#   no re-entry     `echo hi; W=/tmp/x; ...` proves nothing, because clearing it
#                   would mean proving `echo hi` cannot touch W
ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
# The bare read forms, and the only ones substituted. `${W:-/}` is deliberately
# absent: it is not a plain read, and _read_only_mentions rejects the binding
# outright rather than leaving a half-resolved operand behind.
SUBST_NAME_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
# Separators that can terminate a statement and still leave the binding
# standing. `|` and `&` put the assignment in a subshell it never escapes, and
# `||` runs what follows only when the assignment failed.
BINDING_SEPS = (";", "&&", "\n")


def _statement_end(s, i):
    """-> (end, sep) for the statement starting at s[i], quote-aware.

    end is one past the statement's last character. sep is what terminated it:
    ";", "&&", "\\n", "" at end of string, or one of "|", "||", "&" -- which the
    caller must treat as ending the run rather than as another boundary.
    """
    quote, n = None, len(s)
    while i < n:
        c = s[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c in "'\"":
            quote = c
            i += 1
            continue
        if c in ";\n":
            return i, c
        if c in "|&":
            two = s[i:i + 2]
            return i, two if two in ("&&", "||") else c
        i += 1
    return n, ""


def _lone_assignment(stmt):
    """-> (name, value), or (None, None) if stmt is not one literal assignment.

    `export W=/x`, `read W` and the env-prefix `W=/x rm -rf ...` all fail the
    one-word test. The value must be fully literal -- no expansion, no
    substitution (a masked sentinel counts as one) -- so that substituting it
    cannot introduce a fresh expansion for the classifier to resolve again.
    """
    if not ASSIGN_RE.match(stmt):
        return None, None
    try:
        words = shlex.split(stmt)
    except ValueError:
        return None, None
    if len(words) != 1:
        return None, None
    name, _, value = words[0].partition("=")
    if any(c in value for c in ("$", "`", SENTINEL)):
        return None, None
    return name, value


def _read_only_mentions(name, rest):
    """Is every mention of `name` in `rest` a plain $name / ${name} read?

    Anything else drops the binding. One blunt rule stands in for a catalogue of
    every way a shell can rebind a variable, because all of them name it:

        unset W    read W    W+=/x    export W=/x    ((W=1))    for W in ...
        declare W  local W   W=other  eval "W=/"     (W=/)      ${W:-/}

    It over-rejects -- `echo "$W"` keeps its binding but `echo W` loses it --
    which is the direction that costs a prompt rather than a filesystem.
    """
    for m in re.finditer(r"(?<!\w)%s(?!\w)" % re.escape(name), rest):
        s = m.start()
        if s >= 1 and rest[s - 1] == "$":
            continue
        if s >= 2 and rest[s - 2:s] == "${" and rest[m.end():m.end() + 1] == "}":
            continue
        return False
    return True


def leading_bindings(cmd):
    """-> ({name: literal}, rest) for the leading run of lone assignments.

    rest is the remainder of the masked command, which the caller scans for any
    other mention of a bound name.
    """
    masked, _ = mask_substitutions(cmd)
    bindings, i, n = {}, 0, len(masked)
    while i < n:
        while i < n and masked[i] in " \t\n":
            i += 1                      # blank lines between assignments
        if i >= n:
            break
        end, sep = _statement_end(masked, i)
        if sep and sep not in BINDING_SEPS:
            break                       # see _statement_end
        name, value = _lone_assignment(masked[i:end].strip())
        if name is None:
            break
        bindings[name] = value          # last wins, exactly as the shell does
        i = end + len(sep)
    return bindings, masked[i:]


def proven_bindings(cmd):
    """-> {name: literal} the command proves, after both disqualifiers.

    DANGEROUS_VARS is checked here rather than at the point of use, so the deny
    list stays absolute: a same-line `HOME=/tmp/fake` does not unlock
    `rm -rf "$HOME"`. One unexplained exception in that list would cost more
    than the prompt it saves.
    """
    bindings, rest = leading_bindings(cmd)
    return {n: v for n, v in bindings.items()
            if n not in DANGEROUS_VARS and _read_only_mentions(n, rest)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `94/94 passed` (73 baseline + 21 `BINDING_CASES`). No `FAIL` lines. If a binding case fails, the printed `want:` / `got:` are the two dicts — fix the scanner, not the expectation.

- [ ] **Step 5: Commit**

```bash
git add config/.claude/hooks/rm_guard.py config/.claude/hooks/test_rm_guard.py
git commit -F - <<'EOF'
feat(claude): read the bindings a command proves about itself

Nothing consumes these yet. Scanning the raw text rather than the token
stream is the part worth review: shlex eats newlines, so the sequential
form and the env-prefix form tokenize identically, and only the first
proves anything about $W.

Taking just the leading run of lone assignments also makes the binding
unconditional for free, which is what closes the hole where a heredoc
body manufactures one for a variable that is actually unset.
EOF
```

---

### Task 2: substitute and re-classify

**Files:**
- Modify: `config/.claude/hooks/rm_guard.py:411` (`classify_operand` signature and body), `:807` (`rate` inside `analyse_segment`), `:798` (`analyse_segment` signature), `:852` (the `git clean` call site), `:977` (`analyse`, before `in_git = git_segments(toks)`), `:1016` (the `rm` loop call site), `:1023` (the `analyse_segment` call site)
- Modify: `config/.claude/hooks/test_rm_guard.py` — add verdict rows to `CASES`

**Interfaces:**
- Consumes: `proven_bindings(cmd) -> {name: literal}` and `SUBST_NAME_RE` from Task 1
- Produces: `classify_operand(op, cwd, bare_var_ok=True, assigned=None)` and `analyse_segment(argv, cwd, depth, assigned=None)` — both with the new parameter last and defaulted, so no other caller changes

- [ ] **Step 1: Write the failing test**

Append to `CASES` in `config/.claude/hooks/test_rm_guard.py`, after the command-substitution block (after line 160):

```python
    # --- a binding the command itself proves ---------------------------------
    # Same contract as every row above: these are strings handed to the guard on
    # stdin. Nothing here is executed.
    ("binding: literal assignment, then a suffixed use",
     'W=/tmp/build; rm -rf "$W"/*', "allow"),
    ("binding: newline form, which tokenizes like an env prefix",
     'W=/tmp/build\nrm -rf "$W"/*', "allow"),
    ("binding: && terminates a statement", 'W=/tmp/build && rm -rf "$W"/*', "allow"),
    ("binding: two names in one operand", 'A=/tmp; B=x; rm -rf "$A/$B"/*', "allow"),
    # The payoff past rm: every non-rm command is classified with
    # bare_var_ok=False, because `find $W -delete` with W unset is `rm -rf .`.
    ("binding: clears the bare-operand class for find",
     'W=/tmp/x; find "$W" -delete', "allow"),
    # Escalation is free -- the substituted text takes the same path a
    # hand-written literal does, fatal ones included.
    ("binding: escalates when the literal is fatal", 'W=/; rm -rf "$W"/*', "deny"),
    ("binding: last assignment wins", 'W=/tmp/x; W=/; rm -rf "$W"/*', "deny"),
    ("binding: DANGEROUS_VARS is not overridable",
     'HOME=/tmp/fake; rm -rf "$HOME"', "deny"),
    ("binding: resolving is not a blanket pass", 'W=/etc; rm -rf "$W"/*', "ask"),
    ("binding: env prefix proves nothing", 'W=/tmp/x rm -rf "$W"/*', "ask"),
    ("binding: a later mention disqualifies it",
     'W=/tmp/x; unset W; rm -rf "$W"/*', "ask"),
    ("binding: the run must start at offset 0",
     'echo hi; W=/tmp/x; rm -rf "$W"/*', "ask"),
    ("binding: a pipeline puts the assignment in a subshell",
     'W=/tmp/x | cat; rm -rf "$W"/*', "ask"),
    ("binding: the value must be literal", 'W=$HOME; rm -rf "$W"/*', "ask"),
    # The local shell expands a double-quoted remote command before ssh sees it,
    # and shlex has stripped the quotes by now -- so the two cases are
    # indistinguishable and the binding is withheld from the far end.
    ("binding: does not cross ssh", "W=/tmp/x; ssh box 'rm -rf $W/*'", "ask"),
    ("binding: only one name of two resolves",
     'A=/tmp; rm -rf "$A/$B"/*', "ask"),
    ("binding: a heredoc body cannot begin the run",
     "cat <<'EOF' > /tmp/f\nW=/tmp/safe\nEOF\nrm -rf \"$W\"/*", "ask"),
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `94/111 passed`, with 5 `FAIL` lines — the five rows expecting `allow`/`deny` that still come back `ask`:

```
FAIL binding: literal assignment, then a suffixed use
  cmd:  'W=/tmp/build; rm -rf "$W"/*'
  want: allow   got: ask
```

The 12 rows expecting `ask` pass already; they are pinning the disqualifiers, and they must keep passing after Step 3.

- [ ] **Step 3: Write minimal implementation**

**3a.** Change the `classify_operand` signature at line 411 and insert the resolution block right after the command-substitution check (after line 420):

```python
def classify_operand(op, cwd, bare_var_ok=True, assigned=None):
    """-> (decision, reason). Anything not provably safe returns ask/deny.

    bare_var_ok encodes an rm-specific fact: `rm -rf $X` with X unset or empty
    is an *error*, not a catastrophe, so a bare expansion is safe there. It is
    not safe for commands that treat "no path" as "use the current directory" --
    `find $X -delete` with X unset becomes `find -delete`, which is `rm -rf .`.

    assigned holds the names the command itself bound to a literal; see
    proven_bindings.
    """
    if "`" in op or "$(" in op:
        return "ask", "command substitution cannot be statically resolved"

    # A name the command bound to a literal is not an unknown. Substituting and
    # re-classifying puts the result on the same path a hand-written literal
    # takes, which is what makes escalation fall out for free rather than
    # needing a rule of its own: `W=/` resolves to a glob under the root and
    # denies, exactly as `rm -rf /*` does.
    #
    # Recursed WITHOUT assigned, so a literal value that somehow still looks
    # like an expansion cannot loop.
    if assigned:
        resolved = SUBST_NAME_RE.sub(
            lambda m: assigned.get(m.group(1) or m.group(2), m.group(0)), op)
        if resolved != op and not EXPANSION_RE.search(resolved):
            d, r = classify_operand(resolved, cwd, bare_var_ok)
            used = [n for n in dict.fromkeys(
                m.group(1) or m.group(2) for m in SUBST_NAME_RE.finditer(op))
                if n in assigned]
            shown = ", ".join("%s=%s" % (n, assigned[n]) for n in used)
            return d, f"{shown} in this command, so this reads as {resolved}; {r}"
```

Leave the rest of the function body unchanged. A partially-resolved operand — `$A/$B` with only `A` bound — fails the `EXPANSION_RE` test and falls through to today's logic on the original `op`, which is what the "only one name of two resolves" row pins.

**3b.** Thread the parameter through `analyse_segment`. Change its signature at line 798 and its `rate` helper at line 807:

```python
def analyse_segment(argv, cwd, depth, assigned=None):
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
            d, r = classify_operand(t, cwd, bare_var_ok=bare_var_ok,
                                    assigned=assigned)
            out.append(Finding(d, segment, label, t, r))
```

**3c.** The `git clean` branch calls `classify_operand` directly. At line 852:

```python
                d, r = classify_operand(t, cwd, bare_var_ok=False,
                                        assigned=assigned)
```

**3d.** Leave the `ssh` branch's `analyse(remote, "", depth + 1)` call alone, and record why. Insert this comment above it (line 861, inside `elif base == "ssh":`, before `remote = ssh_remote_command(argv)`):

```python
    elif base == "ssh":
        # assigned is deliberately NOT passed into the recursion. The local shell
        # expands a double-quoted remote command before ssh ever runs, and shlex
        # has stripped the quotes by the time we get here, so `ssh h "rm -rf
        # $W/*"` (expanded locally) and `ssh h 'rm -rf $W/*'` (expanded on the
        # far end) are indistinguishable. analyse() re-reads the remote string's
        # own leading run instead, which leaves a local binding unproven there.
        remote = ssh_remote_command(argv)
```

**3e.** In `analyse`, compute the map once. Insert immediately before `in_git = git_segments(toks)` at line 977:

```python
    # What the command proves about its own variables, read from the raw text
    # rather than these tokens -- see leading_bindings for why that is forced.
    assigned = proven_bindings(cmd)

    in_git = git_segments(toks)
```

**3f.** Pass it at the two call sites. In the `rm` loop, line 1016:

```python
        for op in operands:
            d, r = classify_operand(op, cwd, assigned=assigned)
```

And the segment loop, line 1023:

```python
    for start, end in bounds:
        findings.extend(analyse_segment(toks[start:end], cwd, depth, assigned))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `111/111 passed`, no `FAIL` lines.

Then spot-check the reason text by hand, which the verdict table does not cover:

```bash
printf '%s' '{"tool_input":{"command":"W=/tmp/build; rm -rf \"$W\"/*"},"cwd":"/home/u/project"}' \
  | python3 config/.claude/hooks/rm_guard.py
```

Expected: `"permissionDecision": "allow"` and a reason containing `W=/tmp/build in this command, so this reads as /tmp/build/*; literal path under safe root /tmp`.

- [ ] **Step 5: Commit**

```bash
git add config/.claude/hooks/rm_guard.py config/.claude/hooks/test_rm_guard.py
git commit -F - <<'EOF'
feat(claude): clear an operand the command binds to a literal

classify_operand substitutes a proven binding and re-classifies the
result, so the verdict comes off the same path a hand-written literal
takes. Escalation falls out of that rather than needing its own rule:
`W=/; rm -rf "$W"/*` resolves to a glob under the root and denies.

The reach is wider than rm. Every other guarded command is classified
with bare_var_ok=False, because `find $W -delete` with W unset becomes
`find -delete` -- and a proven binding clears that whole class.

Bindings stop at ssh. The local shell expands a double-quoted remote
command before ssh runs and shlex has dropped the quotes by then, so
the two forms are indistinguishable and the far end stays unproven.
EOF
```

---

### Task 3: stop painting a bound name unknowable

**Files:**
- Modify: `config/.claude/hooks/rm_guard.py:117` (`Finding`), `:1058` (`unknown_spans`), `:1087` (`paint_token`), `:1176` + `:1217` + `:1231` (`echo_line` / `render` call sites), and the three Finding construction sites that carry a resolved operand
- Modify: `config/.claude/hooks/test_rm_guard.py` — two rows in `check_render()`, bump `RENDER_CHECKS`

**Interfaces:**
- Consumes: `classify_operand(..., assigned=...)` and `analyse_segment(..., assigned)` from Task 2
- Produces: `Finding` gains a trailing `bound` field (`frozenset` of proven names, default empty); `unknown_spans(tok, bound=frozenset())`; `paint_token(style, tok, bound=frozenset())`

- [ ] **Step 1: Write the failing test**

In `check_render()`, add two rows to the second `for` tuple-list (after the `("the fix is cyan", ...)` row at line 271):

```python
            # Magenta means "the guard cannot know this value". Once the command
            # binds the name to a literal it can, so the operand stays one
            # yellow token -- the same restriction that keeps $HOME undimmed,
            # applied to the other reason a value can be known.
            ("a bound name is not painted unknowable", 'W=/; rm -rf "$W"/*',
             "\x1b[1;33m$W/*\x1b[0m"),
            ("...and the reason says what it resolved to", 'W=/; rm -rf "$W"/*',
             "W=/ in this command, so this reads as //*"),
```

Bump the constant at line 200:

```python
RENDER_CHECKS = 19
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `112/113 passed` with one `FAIL`:

```
FAIL render: a bound name is not painted unknowable
  cmd:  'W=/; rm -rf "$W"/*'
  want: \e[1;33m$W/*\e[0m   got: absent
```

The reason row passes already — Task 2 built that string. Only the colour is wrong: `$W` still comes back `\e[1;35m` because `unknown_spans` cannot see the binding.

- [ ] **Step 3: Write minimal implementation**

**3a.** Add the field to `Finding` at line 117, and document it in the field list above:

```python
#   highlight extra tokens to light up in the echo that are NOT operands and get
#             no row of their own -- the arguments of a construct that hides what
#             it runs, where the complaint is the whole invocation and not one
#             token in it
#   bound     names this command proved to be literals, so the renderer knows
#             which expansions in `operand` are NOT unknowable. Carried per
#             finding rather than per verdict because an ssh'd operand's
#             expansions are unproven even when the local segment's are.
Finding = namedtuple("Finding",
                     "decision segment label operand reason highlight bound",
                     defaults=((), frozenset()))
```

**3b.** Stamp it at the three sites that classify an operand with `assigned`. In `analyse_segment`'s `rate`:

```python
        for t in targets:
            d, r = classify_operand(t, cwd, bare_var_ok=bare_var_ok,
                                    assigned=assigned)
            out.append(Finding(d, segment, label, t, r, (),
                               frozenset(assigned or ())))
```

In the `git clean` branch, replace the `out.append` at line 859:

```python
                out.append(Finding(d, segment, label, t, r, (),
                                   frozenset(assigned or ())))
```

In `analyse`'s `rm` loop, replace the `findings.append` at line 1019:

```python
            findings.append(Finding(d, segment, None, op, r, (),
                                    frozenset(assigned)))
```

The `ssh` branch's `_replace(label=...)` is untouched, so a remote finding keeps the empty `bound` its own `analyse` gave it.

**3c.** Teach `unknown_spans` about it (line 1058). Add the parameter and one skip, and extend the docstring:

```python
def unknown_spans(tok, bound=frozenset()):
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
    undercuts the advice. A name in `bound` is skipped for the same reason
    $HOME is -- the command assigned it a literal, so its value is known.
    """
    spans = subst_spans(tok)
    for m in EXPANSION_RE.finditer(tok):
        s, e = m.span()
        if e - s == len(tok) or GUARDED_RE.match(m.group(0)):
            continue
        name = EXP_NAME_RE.match(m.group(0))
        if name and name.group(1) in bound:
            continue
        if any(a <= s < b for a, b in spans):
            continue  # already inside a substitution: $(cat $F) is one opaque span
        spans.append((s, e))
    return sorted(spans)
```

**3d.** Thread it through `paint_token` (line 1087):

```python
def paint_token(style, tok, bound=frozenset()):
    """Paint tok in `style`, except for its unknowable spans, which go UNKNOWN.

    Callers clip first, so a span cut in half by the truncation simply fails to
    parse and comes back in the base style -- never as a severed escape.
    """
    spans = unknown_spans(tok, bound)
```

**3e.** Pass it from the two render paths. In `echo_line`, add the parameter and use it in the final join (line 1145 and line 1176):

```python
def echo_line(toks, flagged, bound=frozenset()):
```

```python
    return " ".join(paint_token(style(i), p, bound) for i, p in pieces)
```

In `render`, derive it per block and pass it to both painters (lines 1213-1232):

```python
    for segment, label, group in blocks(findings):
        out.append("")
        bound = frozenset().union(*(f.bound for f in group)) if group else frozenset()
        if segment:
            lit = {f.operand for f in group if f.operand}
            lit.update(t for f in group for t in f.highlight)
            out.append("  " + echo_line(list(segment), lit, bound))
```

```python
            op = clip(f.operand)
            out.append("  " + paint_token(YELLOW, op, bound)
                       + " " * (width - len(op) + 2) + paint_marks(f.reason))
```

`paint_marks` is left alone: it paints marked spans in bail-out reasons, and a bail-out never carries a resolved operand.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `113/113 passed`, no `FAIL` lines. The existing render rows matter most here — `("unguarded expansion splits from its suffix", 'rm -rf "$W"/*', ...)` must still find `$W` magenta, because that command binds nothing.

Then look at the deny by eye, since colour is the whole point of this task:

```bash
printf '%s' '{"tool_input":{"command":"W=/; rm -rf \"$W\"/*"},"cwd":"/home/u/project"}' \
  | python3 config/.claude/hooks/rm_guard.py | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["permissionDecisionReason"])'
```

Expected: `$W/*` renders as one yellow token, and the reason reads `W=/ in this command, so this reads as //*; glob directly under the filesystem root`.

- [ ] **Step 5: Commit**

```bash
git add config/.claude/hooks/rm_guard.py config/.claude/hooks/test_rm_guard.py
git commit -F - <<'EOF'
fix(claude): stop dimming a bound name to "cannot know"

Magenta is the one colour that means the guard cannot resolve a span,
and it is what separates an operand nothing can read from one that
resolved to something fatal. A name the command assigned a literal
belongs on the $HOME side of that line: known, and flagged for what it
is known to be.

Carried per finding rather than per verdict, because an operand reached
over ssh is unproven even when the local segment's operands are not.
EOF
```

---

## Self-Review

**1. Spec coverage.** Every section maps to a task:

| Spec section | Task |
|---|---|
| Where bindings come from (raw text, leading run, `_statement_end` separators, `_lone_assignment`) | 1 |
| What disqualifies a binding (other-mention, `DANGEROUS_VARS`) | 1 |
| How a binding is used (substitute, re-classify, fall through, escalation) | 2 |
| ssh (bindings withheld) | 2, step 3d |
| Rendering (`unknown_spans` suppression, reason text) | 2 builds the reason, 3 fixes the colour |
| Verdict table (16 rows) | 2, plus 21 scanner-level rows in Task 1 |
| Cost model (three structural properties) | comments in Task 1's `leading_bindings` header |
| Not doing (`CLAUDE.md`, non-literal values, `rclone` scope gap) | out of scope by design; no task |

**2. Placeholders.** None — every code step carries the complete text to insert, every run step names the command and its expected output, and the two hand-checks give exact `printf` invocations.

**3. Type consistency.** `proven_bindings` returns `{name: literal}` and is called with that name in Task 2 (`assigned = proven_bindings(cmd)`). `assigned` is the parameter name in `classify_operand`, `analyse_segment`, and `rate` throughout. `bound` is a `frozenset` everywhere: built via `frozenset(assigned or ())` at the three Finding sites, defaulted to `frozenset()` in `unknown_spans` / `paint_token` / `echo_line`, and unioned in `render`. `SUBST_NAME_RE` is defined in Task 1 and first used in Task 2. Test counts chain correctly: 73 → 94 → 111 → 113.
