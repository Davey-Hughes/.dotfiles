# rm guard assignment tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `rm_guard.py` clear a destructive operand whose variable the command itself assigns to a literal, so `W=/tmp/build; rm -rf "$W"/*` returns `allow` instead of `ask`.

**Architecture:** A pure function walks `segment_bounds` for a leading run of segments that are each a single `NAME=VALUE` token, filters the result through two disqualifiers, and hands the surviving map to `classify_operand`, which substitutes and re-classifies. This works at the token level only because `mask_opaque` now normalises unquoted newlines to `;` (commit `e3c8e72`) — before that, the sequential and env-prefix forms tokenized identically and the scan had to be done on raw text.

**Tech Stack:** Python 3 stdlib only (`re`, `shlex`, `posixpath`, `os`, `json`). No third-party packages, no test framework — `test_rm_guard.py` is a plain script.

## Global Constraints

- **Never execute any command string from this plan or from the test tables.** Every dangerous string here is *data*: `test_rm_guard.py` serialises it into a JSON payload and writes it to the guard's stdin, and the guard only classifies text. A `rm -rf /` in `CASES` is safe precisely because nothing runs it. Do not paste these strings into a shell to "check" them.
- Full paths: guard is `config/.claude/hooks/rm_guard.py`, tests are `config/.claude/hooks/test_rm_guard.py`. `~/.config/.claude` is a folded symlink to `config/.claude`, so editing the repo file edits the live hook — no install step, and a broken guard affects the running session immediately.
- Run the suite with `python3 config/.claude/hooks/test_rm_guard.py` from the repo root. It prints `N/M passed` and exits non-zero on any failure.
- Baseline before you start: **80/80 passed** (`len(CASES)`=58 + wrapper 5 + `RENDER_CHECKS`=17). Confirm this before Task 1 and treat any drop as a regression you caused.
- Existing style is load-bearing: every non-obvious rule in this file carries a comment explaining *why*, in prose, referencing the failure it prevents. Match it. Do not add comments that restate the code.
- Do not add attribution to commit messages (no `Co-Authored-By`, no `Generated with`).
- `git status` must be clean at the end of each task's commit.

---

### Task 1: `tokenize` — one place that builds the token stream

**Files:**
- Modify: `config/.claude/hooks/rm_guard.py` — add `tokenize` next to `mask_opaque`; call it from `analyse`

**Interfaces:**
- Consumes: `mask_opaque(cmd) -> (masked, subs)`, `unmask(tok, subs)`
- Produces: `tokenize(cmd) -> [str]`, raising `ValueError` on an unparseable command

This is a pure refactor with no behaviour change. It exists so Task 2's tests can build the same token list `analyse` does without copying the incantation.

- [ ] **Step 1: Add the helper**

Insert after `has_subst` in `config/.claude/hooks/rm_guard.py`:

```python
def tokenize(cmd):
    """-> tokens, with opaque spans masked and unquoted newlines normalised.

    Raises ValueError on an unbalanced quote, which analyse() turns into a
    bail-out. Split out so the binding scan and its tests read the same stream
    analyse() does; two copies of this would drift.
    """
    masked, subs = mask_opaque(cmd)
    lex = shlex.shlex(masked, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return [unmask(t, subs) for t in lex]
```

- [ ] **Step 2: Use it in `analyse`**

Replace the tokenizing block in `analyse` — the four lines from `masked, subs = mask_opaque(cmd)` through `toks = [unmask(t, subs) for t in lex]`, keeping the surrounding `try` and its comment intact:

```python
    try:
        toks = tokenize(cmd)
    except ValueError as e:
```

- [ ] **Step 3: Run the suite to verify nothing moved**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `80/80 passed`. A pure refactor must not change a single verdict; any change means the two code paths were not equivalent.

- [ ] **Step 4: Commit**

```bash
git add config/.claude/hooks/rm_guard.py
git commit -F - <<'EOF'
refactor(claude): give the token stream one construction site

No behaviour change. The binding scan in the next commit needs the same
tokens analyse() sees, and a second copy of the mask_opaque + shlex
incantation would drift from this one.
EOF
```

---

### Task 2: `proven_bindings` — read what the command proves about its own variables

**Files:**
- Modify: `config/.claude/hooks/rm_guard.py` — new section after `segment_bounds` / `command_word` / `git_segments`, before `# --- per-command target extraction ---`
- Modify: `config/.claude/hooks/test_rm_guard.py` — add `BINDING_CASES` + `check_bindings()`, wire into `main()`

**Interfaces:**
- Consumes: `tokenize(cmd)` from Task 1; `segment_bounds(toks) -> [(start, end)]`; `ENV_ASSIGN_RE` (line ~220); `DANGEROUS_VARS` (line ~145); `SENTINEL`
- Produces:
  - `proven_bindings(toks, bounds) -> {name: literal}` — the entry point Task 3 uses
  - `_read_only_mentions(name, toks) -> bool`
  - `BINDING_SEPS`, `SUBST_NAME_RE` (module constants; `SUBST_NAME_RE` is unused until Task 3)

- [ ] **Step 1: Write the failing test**

Extend the import in `config/.claude/hooks/test_rm_guard.py`:

```python
from rm_guard import MARK, proven_bindings, segment_bounds, tokenize  # noqa: E402
```

Add this block immediately after the `CASES` list ends, before `def check_wrapper():`:

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
    # Tokenized identically to the env-prefix form below until mask_opaque began
    # normalising unquoted newlines to ";".
    ('W=/tmp/build\nrm -rf "$W"/*', {"W": "/tmp/build"}),
    ('W=/tmp/build && rm -rf "$W"/*', {"W": "/tmp/build"}),
    ('A=/tmp; B=x; rm -rf "$A/$B"/*', {"A": "/tmp", "B": "x"}),
    # shlex has already stripped the quotes, so the value arrives whole.
    ('W="/tmp/my build"; rm -rf "$W"/*', {"W": "/tmp/my build"}),
    # Last wins, exactly as the shell does.
    ('W=/tmp/x; W=/; rm -rf "$W"/*', {"W": "/"}),
    # A plain read does not disqualify. This is the permissive edge of the
    # other-mention rule and it is deliberate: the alternative is deciding which
    # commands are read-only, which is the reasoning this guard refuses to do.
    ('W=/tmp/x; echo "$W"; rm -rf "$W"/*', {"W": "/tmp/x"}),

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
    # `then W=/tmp/x` is two tokens, so a conditional branch cannot bind.
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
]


def check_bindings():
    """proven_bindings() in isolation. Nothing here is executed -- see module doc."""
    fails = []
    for cmd, want in BINDING_CASES:
        toks = tokenize(cmd)
        got = proven_bindings(toks, segment_bounds(toks))
        if got != want:
            fails.append(("bindings", cmd, want, got, ""))
    return fails
```

In `main()`, add the call after `fails += check_render()` and fold the count into `total`:

```python
    fails += check_bindings()
```

```python
    total = len(CASES) + 5 + RENDER_CHECKS + len(BINDING_CASES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: a traceback, not a test failure — `ImportError: cannot import name 'proven_bindings' from 'rm_guard'`. That is the correct failure here; the function does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Insert after `git_segments` in `config/.claude/hooks/rm_guard.py`, before the `# --- per-command target extraction ---` banner:

```python
# --- bindings the command proves about itself --------------------------------
# `rm -rf "$W"/*` is unprovable on its own and rightly asks. It stops being
# unprovable when the same command assigns W a literal first:
#
#     W=/tmp/build; rm -rf "$W"/*
#
# Only the LEADING run of lone-assignment segments counts, which is a narrow rule
# that buys three properties a general dataflow pass would each have to earn:
#
#   unconditional   nothing has run before them, so no branch and no loop can
#                   have skipped them
#   not data        a heredoc body arrives as one sentinel token, so it cannot
#                   supply a binding no matter what it says
#   no re-entry     `echo hi; W=/tmp/x; ...` proves nothing, because clearing it
#                   would mean proving `echo hi` cannot touch W
#
# This reads the token stream, which only works because mask_opaque normalises
# unquoted newlines to ";". Before that, shlex consumed a newline as whitespace
# and these two were the same token list:
#
#     W=/tmp/build\nrm -rf "$W"/*    sequential -- the binding holds
#     W=/tmp/build rm -rf "$W"/*     env prefix -- it does NOT, because the shell
#                                    expands $W from the OUTER scope before the
#                                    assignment takes effect
#
# Now the first is two segments and the second is one, so telling them apart is a
# length test rather than a second parser.
SUBST_NAME_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
# Separators that continue the run. `|` and `&` put the assignment in a subshell
# it never escapes; `||` runs what follows only when the assignment failed.
BINDING_SEPS = (";", "&&")


def _read_only_mentions(name, toks):
    """Is every mention of `name` in toks a plain $name / ${name} read?

    Anything else drops the binding. One blunt rule stands in for a catalogue of
    every way a shell can rebind a variable, because all of them name it:

        unset W    read W    W+=/x    export W=/x    ((W=1))    for W in ...
        declare W  local W   W=other  eval "W=/"     (W=/)      ${W:-/}

    It over-rejects -- `echo "$W"` keeps its binding but `echo W` loses it --
    which is the direction that costs a prompt rather than a filesystem.
    """
    word = re.compile(r"(?<!\w)%s(?!\w)" % re.escape(name))
    for t in toks:
        for m in word.finditer(t):
            s = m.start()
            if s >= 1 and t[s - 1] == "$":
                continue
            if s >= 2 and t[s - 2:s] == "${" and t[m.end():m.end() + 1] == "}":
                continue
            return False
    return True


def proven_bindings(toks, bounds):
    """-> {name: literal} the command proves, after both disqualifiers.

    DANGEROUS_VARS is checked here rather than at the point of use, so the deny
    list stays absolute: a same-line `HOME=/tmp/fake` does not unlock
    `rm -rf "$HOME"`. One unexplained exception in that list would cost more than
    the prompt it saves.
    """
    bindings, rest_at = {}, 0
    for start, stop in bounds:
        seg = toks[start:stop]
        if len(seg) != 1 or not ENV_ASSIGN_RE.match(seg[0]):
            break
        # The separator AFTER the segment; end of input terminates it cleanly.
        if stop < len(toks) and toks[stop] not in BINDING_SEPS:
            break
        name, _, value = seg[0].partition("=")
        # A literal value cannot reintroduce something to resolve. SENTINEL
        # catches a masked heredoc body or substitution that unmask left behind.
        if any(c in value for c in ("$", "`", SENTINEL)):
            break
        bindings[name] = value          # last wins, exactly as the shell does
        rest_at = stop + 1
    rest = toks[rest_at:]
    return {n: v for n, v in bindings.items()
            if n not in DANGEROUS_VARS and _read_only_mentions(n, rest)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `102/102 passed` (80 baseline + 22 `BINDING_CASES`). No `FAIL` lines. If a binding case fails, the printed `want:` / `got:` are the two dicts — fix the scanner, not the expectation.

- [ ] **Step 5: Commit**

```bash
git add config/.claude/hooks/rm_guard.py config/.claude/hooks/test_rm_guard.py
git commit -F - <<'EOF'
feat(claude): read the bindings a command proves about itself

Nothing consumes these yet. A leading run of lone-assignment segments is
a narrow enough rule to be worth the reach: the bindings are
unconditional, a heredoc body cannot supply one because it arrives as a
single sentinel token, and any other mention of the name drops it.

Reads the token stream rather than the raw text, which only became
possible once mask_opaque started normalising newlines -- until then the
sequential form and the env-prefix form, which prove opposite things,
tokenized to the same list.
EOF
```

---

### Task 3: substitute and re-classify

**Files:**
- Modify: `config/.claude/hooks/rm_guard.py` — `classify_operand` signature and body; `analyse_segment` signature and its `rate` helper; the `git clean` call site; the `ssh` branch comment; `analyse` (compute the map, two call sites)
- Modify: `config/.claude/hooks/test_rm_guard.py` — add verdict rows to `CASES`

**Interfaces:**
- Consumes: `proven_bindings(toks, bounds)` and `SUBST_NAME_RE` from Task 2
- Produces: `classify_operand(op, cwd, bare_var_ok=True, assigned=None)` and `analyse_segment(argv, cwd, depth, assigned=None)` — the new parameter is last and defaulted, so no other caller changes

- [ ] **Step 1: Write the failing test**

Append to `CASES`, after the command-substitution block:

```python
    # --- a binding the command itself proves ---------------------------------
    # Same contract as every row above: these are strings handed to the guard on
    # stdin. Nothing here is executed.
    ("binding: literal assignment, then a suffixed use",
     'W=/tmp/build; rm -rf "$W"/*', "allow"),
    ("binding: newline form", 'W=/tmp/build\nrm -rf "$W"/*', "allow"),
    ("binding: && continues the run", 'W=/tmp/build && rm -rf "$W"/*', "allow"),
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
    ("binding: the run must start at the first segment",
     'echo hi; W=/tmp/x; rm -rf "$W"/*', "ask"),
    ("binding: a pipeline puts the assignment in a subshell",
     'W=/tmp/x | cat; rm -rf "$W"/*', "ask"),
    ("binding: the value must be literal", 'W=$HOME; rm -rf "$W"/*', "ask"),
    # The local shell expands a double-quoted remote command before ssh sees it,
    # and shlex has stripped the quotes by now -- so the two cases are
    # indistinguishable and the binding is withheld from the far end.
    ("binding: does not cross ssh", "W=/tmp/x; ssh box 'rm -rf $W/*'", "ask"),
    ("binding: only one name of two resolves", 'A=/tmp; rm -rf "$A/$B"/*', "ask"),
    ("binding: a heredoc body cannot bind",
     "cat <<'EOF' > /tmp/f\nW=/tmp/safe\nEOF\nrm -rf \"$W\"/*", "ask"),
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `102/119 passed`, with 5 `FAIL` lines — the five rows expecting `allow`/`deny` that still come back `ask`:

```
FAIL binding: literal assignment, then a suffixed use
  cmd:  'W=/tmp/build; rm -rf "$W"/*'
  want: allow   got: ask
```

The 12 rows expecting `ask` pass already; they pin the disqualifiers, and they must keep passing after Step 3.

- [ ] **Step 3: Write minimal implementation**

**3a.** Change the `classify_operand` signature and insert the resolution block after the sentinel check:

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
```

Then, immediately after the `if SENTINEL in op:` arm:

```python
    # A name the command bound to a literal is not an unknown. Substituting and
    # re-classifying puts the result on the same path a hand-written literal
    # takes, which is what makes escalation fall out for free rather than
    # needing a rule of its own: `W=/` resolves to a glob under the root and
    # denies, exactly as `rm -rf /*` does.
    #
    # Recursed WITHOUT assigned, so a value that somehow still looks like an
    # expansion cannot loop.
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

Leave the rest of the function unchanged. A partially-resolved operand — `$A/$B` with only `A` bound — fails the `EXPANSION_RE` test and falls through to today's logic on the original `op`, which is what the "only one name of two resolves" row pins.

**3b.** Thread the parameter through `analyse_segment` and its `rate` helper:

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

**3c.** The `git clean` branch calls `classify_operand` directly:

```python
                d, r = classify_operand(t, cwd, bare_var_ok=False,
                                        assigned=assigned)
```

**3d.** Leave the `ssh` branch's `analyse(remote, "", depth + 1)` alone, and record why. Insert above `remote = ssh_remote_command(argv)`:

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

**3e.** In `analyse`, compute the map once. Insert immediately before `in_git = git_segments(toks)`:

```python
    # What the command proves about its own variables.
    assigned = proven_bindings(toks, bounds)

    in_git = git_segments(toks)
```

**3f.** Pass it at the two call sites — the `rm` loop:

```python
        for op in operands:
            d, r = classify_operand(op, cwd, assigned=assigned)
```

and the segment loop:

```python
    for start, end in bounds:
        findings.extend(analyse_segment(toks[start:end], cwd, depth, assigned))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `119/119 passed`, no `FAIL` lines.

Then spot-check the reason text, which the verdict table does not cover:

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
command before ssh runs and shlex has dropped the quotes by then, so the
two forms are indistinguishable and the far end stays unproven.
EOF
```

---

### Task 4: stop painting a bound name unknowable

**Files:**
- Modify: `config/.claude/hooks/rm_guard.py` — `Finding`; `unknown_spans`; `paint_token`; `echo_line`; `render`; the three Finding sites that carry a resolved operand
- Modify: `config/.claude/hooks/test_rm_guard.py` — two rows in `check_render()`, bump `RENDER_CHECKS`

**Interfaces:**
- Consumes: `classify_operand(..., assigned=...)` and `analyse_segment(..., assigned)` from Task 3
- Produces: `Finding` gains a trailing `bound` field (`frozenset` of proven names, default empty); `unknown_spans(tok, bound=frozenset())`; `paint_token(style, tok, bound=frozenset())`; `echo_line(toks, flagged, bound=frozenset())`

- [ ] **Step 1: Write the failing test**

Add two rows to the second tuple-list in `check_render()`, after the `("the fix is cyan", ...)` row:

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

Bump the constant:

```python
RENDER_CHECKS = 19
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `120/121 passed` with one `FAIL`:

```
FAIL render: a bound name is not painted unknowable
  cmd:  'W=/; rm -rf "$W"/*'
  want: \e[1;33m$W/*\e[0m   got: absent
```

The reason row passes already — Task 3 built that string. Only the colour is wrong: `$W` still comes back `\e[1;35m` because `unknown_spans` cannot see the binding.

- [ ] **Step 3: Write minimal implementation**

**3a.** Add the field to `Finding`, documenting it in the field list above:

```python
#   bound     names this command proved to be literals, so the renderer knows
#             which expansions in `operand` are NOT unknowable. Carried per
#             finding rather than per verdict because an ssh'd operand's
#             expansions are unproven even when the local segment's are.
Finding = namedtuple("Finding",
                     "decision segment label operand reason highlight bound",
                     defaults=((), frozenset()))
```

**3b.** Stamp it at the three sites that classify an operand with `assigned`. In `rate`:

```python
        for t in targets:
            d, r = classify_operand(t, cwd, bare_var_ok=bare_var_ok,
                                    assigned=assigned)
            out.append(Finding(d, segment, label, t, r, (),
                               frozenset(assigned or ())))
```

In the `git clean` branch:

```python
                out.append(Finding(d, segment, label, t, r, (),
                                   frozenset(assigned or ())))
```

In `analyse`'s `rm` loop:

```python
            findings.append(Finding(d, segment, None, op, r, (),
                                    frozenset(assigned)))
```

The `ssh` branch's `_replace(label=...)` is untouched, so a remote finding keeps the empty `bound` its own `analyse` gave it.

**3c.** Teach `unknown_spans` about it — add the parameter, one skip, and a docstring line:

```python
def unknown_spans(tok, bound=frozenset()):
```

```python
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

**3d.** Thread it through `paint_token`:

```python
def paint_token(style, tok, bound=frozenset()):
```

```python
    spans = unknown_spans(tok, bound)
```

**3e.** Thread it through `echo_line` and `render`:

```python
def echo_line(toks, flagged, bound=frozenset()):
```

```python
    return " ".join(paint_token(style(i), p, bound) for i, p in pieces)
```

In `render`, derive it per block and pass it to both painters:

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

Expected: `121/121 passed`, no `FAIL` lines. The existing render rows matter most here — `("unguarded expansion splits from its suffix", 'rm -rf "$W"/*', ...)` must still find `$W` magenta, because that command binds nothing.

Then look at the deny by eye, since colour is the whole point of this task:

```bash
printf '%s' '{"tool_input":{"command":"W=/; rm -rf \"$W\"/*"},"cwd":"/home/u/project"}' \
  | python3 config/.claude/hooks/rm_guard.py \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["permissionDecisionReason"])'
```

Expected: `$W/*` renders as one yellow token, and the reason reads `W=/ in this command, so this reads as //*; glob directly under the filesystem root`.

- [ ] **Step 5: Commit**

```bash
git add config/.claude/hooks/rm_guard.py config/.claude/hooks/test_rm_guard.py
git commit -F - <<'EOF'
fix(claude): stop dimming a bound name to "cannot know"

Magenta is the one colour that means the guard cannot resolve a span, and
it is what separates an operand nothing can read from one that resolved
to something fatal. A name the command assigned a literal belongs on the
$HOME side of that line: known, and flagged for what it is known to be.

Carried per finding rather than per verdict, because an operand reached
over ssh is unproven even when the local segment's operands are not.
EOF
```

---

## Self-Review

**1. Spec coverage.** Every section maps to a task:

| Spec section | Task |
|---|---|
| Where bindings come from (token-level leading run, separator rule, literal value, `tokenize` helper) | 1, 2 |
| What disqualifies a binding (other-mention, `DANGEROUS_VARS`) | 2 |
| How a binding is used (substitute, re-classify, fall through, escalation) | 3 |
| ssh (bindings withheld) | 3, step 3d |
| Rendering (`unknown_spans` suppression, reason text) | 3 builds the reason, 4 fixes the colour |
| Verdict table (17 rows) | 3, plus 22 scanner-level rows in Task 2 |
| Cost model (three structural properties) | comments in Task 2's section header |
| Not doing (`CLAUDE.md`, non-literal values, `rclone` scope gap) | out of scope by design; no task |

**2. Placeholders.** None — every code step carries the complete text to insert, every run step names the command and its expected output, and the two hand-checks give exact `printf` invocations.

**3. Type consistency.** `proven_bindings(toks, bounds)` returns `{name: literal}` and is called with that signature in Task 2's test (`proven_bindings(toks, segment_bounds(toks))`) and in Task 3 (`proven_bindings(toks, bounds)`). `assigned` is the parameter name in `classify_operand`, `analyse_segment`, and `rate` throughout. `bound` is a `frozenset` everywhere: built via `frozenset(assigned or ())` at the three Finding sites, defaulted in `unknown_spans` / `paint_token` / `echo_line`, unioned in `render`. `tokenize` is defined in Task 1 and used in Tasks 1 and 2. `SUBST_NAME_RE` is defined in Task 2 and first used in Task 3. Test counts chain: 80 → 80 → 102 → 119 → 121.
