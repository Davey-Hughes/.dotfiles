# rm guard rewrite + deny-only bindings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite unguarded `$VAR` to `${VAR:?}` in destructive commands via `updatedInput`, so approving a prompt is safe against the empty-expansion catastrophe; and wire the existing binding map in as a **deny-only** escalator.

**Architecture:** Two independent additions to `rm_guard.py`. Neither grants a new `allow`. `proven_bindings` (already built and hardened) is consulted only to escalate `ask` → `deny`. A new quote-aware rewrite pass produces `updatedInput` alongside the existing `ask`.

**Spec:** `docs/superpowers/specs/2026-07-30-rm-guard-rewrite-and-deny-design.md` — authoritative. Read the "form is not the warrant" section before Task 2; it is why the rewrite must not allow.

**Tech Stack:** Python 3 stdlib only. No test framework — `test_rm_guard.py` is a plain script.

## Global Constraints

- **Never execute any command string from the test tables.** They are data: the suite serialises each into a JSON payload and writes it to the guard's stdin, and the guard only classifies text. A `rm -rf /` there is safe precisely because nothing runs it. Do not paste them into a shell.
- `~/.config/.claude` is a folded symlink to `config/.claude`, so `config/.claude/hooks/rm_guard.py` **is** the live hook for the running session. A broken guard breaks tooling immediately.
- Suite: `python3 config/.claude/hooks/test_rm_guard.py` from the repo root, printing `N/M passed`. Starting point: **131/131**.
- **Neither task may turn any current `ask` or `deny` into an `allow`.** That is the invariant the whole design rests on. If a change would, stop and report rather than adjusting a test expectation.
- House style: comments explain *why*, in prose, naming the failure the rule prevents. No comments that restate code.
- No commit attribution (`Co-Authored-By`, `Generated with`).
- `git status` clean at the end of each task.

---

### Task 1: bindings escalate, and only escalate

**Files:** `config/.claude/hooks/rm_guard.py`, `config/.claude/hooks/test_rm_guard.py`

**Interfaces:**
- Consumes: `proven_bindings(cmd) -> {name: literal}`, `SUBST_NAME_RE`, `EXPANSION_RE`, `DANGEROUS_VARS` — all already in the file
- Produces: `classify_operand(op, cwd, bare_var_ok=True, assigned=None)`, `analyse_segment(argv, cwd, depth, assigned=None)`

- [ ] **Step 1: Write the failing test**

Add to `CASES`:

```python
    # --- a binding may condemn an operand, never clear one -------------------
    # proven_bindings produced six wrong-`allow` defects across six review
    # rounds, every one a stale binding trusted to prove safety. Consulted only
    # to escalate, a stale binding costs a missed deny -- a prompt -- instead.
    ("binding escalates a fatal literal", 'W=/; rm -rf "$W"/*', "deny"),
    ("binding does not clear a safe literal",
     'W=/tmp/build; rm -rf "$W"/*', "ask"),
    ("binding does not clear an out-of-bounds literal",
     'W=/etc; rm -rf "$W"/*', "ask"),
    ("binding escalates through the find arm", 'W=/; find "$W" -delete', "deny"),
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `133/135 passed`, with 2 `FAIL` lines — the two rows expecting `deny` come back `ask`. The two rows expecting `ask` pass already and must keep passing; they are what pins "never clears".

- [ ] **Step 3: Write minimal implementation**

Change `classify_operand`'s signature and insert the escalation after the `SENTINEL` arm:

```python
def classify_operand(op, cwd, bare_var_ok=True, assigned=None):
```

```python
    # A binding is consulted to CONDEMN an operand and never to clear one. The
    # map is built by static inference over shell text, and six review rounds
    # found six ways to make it claim a binding the shell had already
    # overwritten -- each of which would have been a wrong `allow` on
    # `rm -rf /*`. Escalation inverts that: a stale binding here costs a missed
    # deny, which is a prompt. So `deny` propagates and every other verdict
    # falls through to the unresolved operand's own classification.
    if assigned:
        resolved = SUBST_NAME_RE.sub(
            lambda m: assigned.get(m.group(1) or m.group(2), m.group(0)), op)
        if resolved != op and not EXPANSION_RE.search(resolved):
            d, r = classify_operand(resolved, cwd, bare_var_ok)
            if d == "deny":
                used = [n for n in dict.fromkeys(
                    m.group(1) or m.group(2) for m in SUBST_NAME_RE.finditer(op))
                    if n in assigned]
                shown = ", ".join("%s=%s" % (n, assigned[n]) for n in used)
                return d, f"{shown} in this command, so this reads as {resolved}; {r}"
```

Thread `assigned` through `analyse_segment(argv, cwd, depth, assigned=None)`, its `rate` helper (`classify_operand(t, cwd, bare_var_ok=bare_var_ok, assigned=assigned)`), and the `git clean` call site. Leave the `ssh` branch's `analyse(remote, "", depth + 1)` alone and say why above it:

```python
    elif base == "ssh":
        # assigned is deliberately NOT passed into the recursion. The local shell
        # expands a double-quoted remote command before ssh runs, and shlex has
        # stripped the quotes by now, so `ssh h "rm -rf $W/*"` and
        # `ssh h 'rm -rf $W/*'` are indistinguishable here. analyse() re-reads
        # the remote string's own bindings instead.
        remote = ssh_remote_command(argv)
```

In `analyse`, before `in_git = git_segments(toks)`:

```python
    # Consulted only to escalate; see classify_operand.
    assigned = proven_bindings(cmd)

    in_git = git_segments(toks)
```

and pass it at the two call sites — `classify_operand(op, cwd, assigned=assigned)` in the `rm` loop, and `analyse_segment(toks[start:end], cwd, depth, assigned)` in the segment loop.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `135/135 passed`.

Then confirm the invariant directly — no verdict became `allow`:

```bash
python3 - <<'PYEOF'
import json, subprocess, sys
sys.path.insert(0, "config/.claude/hooks")
import test_rm_guard as t
worse = []
for name, cmd, want in t.CASES:
    got, _ = t.verdict(cmd)
    if got != want:
        worse.append((name, want, got))
print("mismatches:", worse or "none")
PYEOF
```

Expected: `mismatches: none`.

- [ ] **Step 5: Commit**

```bash
git add config/.claude/hooks/rm_guard.py config/.claude/hooks/test_rm_guard.py
git commit -F - <<'EOF'
feat(claude): let a binding condemn an operand, never clear one

proven_bindings is static inference over shell text, and six review
rounds found six ways to make it claim a binding the shell had already
overwritten -- quoted assignment words, an existence check that let one
assignment vouch for another, a scanner blind to backslashes, and eval
reached through a wrapper. Every one would have been a wrong allow on
rm -rf /*.

Consulted only to escalate, all six become a missed deny, which is a
prompt. So deny propagates and every other verdict falls through to the
unresolved operand's own classification.
EOF
```

---

### Task 2: rewrite `$VAR` to `${VAR:?}` via updatedInput

> **Task 2 was reworked during execution.** Its body below specifies
> `rewrite_guarded`, which *edited* the command in place, and a quote-state
> machine to locate each `$VAR`. That machine had no `#`-comment handling, so an
> apostrophe in `# don't touch src` inverted its polarity and it rewrote an
> unrelated `sed -i 's/$W/OK/'` script while leaving the `rm` unguarded. It was
> replaced by `guard_prefix`, which prepends `: "${NAME:?...}"; ` and leaves the
> command body byte-identical — no lexer, so no polarity to invert. The
> `REWRITE_CHECKS = 7` constant and the `HEREDOC_RE`/single-quote skip logic
> below no longer exist. **The spec is authoritative**, and `guard_prefix`'s own
> docstring is the real record.

**Files:** `config/.claude/hooks/rm_guard.py`, `config/.claude/hooks/test_rm_guard.py`

**Interfaces:**
- Consumes: `SUBST_NAME_RE`, `HEREDOC_RE`, `DANGEROUS_VARS`, `GUARDED_RE`
- Produces: `rewrite_guarded(cmd, names) -> str`, `rewritable_names(findings) -> [str]`, `emit(decision, reason, updated=None)`

Read the spec's "form is not the warrant" section first. The verdict stays `ask`. A rewrite that returned `allow` would launder an unverified command into the "author vouched for this" category — `${W:?}` proves non-empty, not safe, and `W=/etc` from the environment sails through it.

- [ ] **Step 1: Write the failing test**

Add a checker beside `check_render`, and call it from `main()`:

```python
REWRITE_CHECKS = 7


def check_rewrite():
    """The guard must hand back a safer command, and never a different one.

    updatedInput replaces the command before it runs, so a bug here changes what
    executes rather than merely what is reported. The direction is bounded --
    ${W:?} either expands exactly as $W did or aborts the shell -- but the
    boundary is only held by these rows.
    """
    fails = []

    def want(name, cmd, expect_cmd, expect_decision="ask"):
        p = subprocess.run([sys.executable, GUARD], input=payload(cmd),
                           capture_output=True, text=True)
        out = json.loads(p.stdout)["hookSpecificOutput"] if p.stdout.strip() else {}
        got = out.get("updatedInput", {}).get("command")
        if got != expect_cmd or out.get("permissionDecision") != expect_decision:
            fails.append((f"rewrite: {name}", cmd, f"{expect_decision} {expect_cmd!r}",
                          f"{out.get('permissionDecision')} {got!r}", ""))

    want("suffixed expansion", 'rm -rf "$W"/*',
         'rm -rf "${W:?refusing to delete under an empty path}"/*')
    want("braced form", 'rm -rf "${W}"/*',
         'rm -rf "${W:?refusing to delete under an empty path}"/*')
    want("bare operand under find", 'find "$W" -delete',
         'find "${W:?refusing to delete under an empty path}" -delete')
    # No expansion happens inside single quotes, so rewriting there would change
    # literal text rather than behaviour.
    want("single quotes are left literal", 'echo \'$W\'; rm -rf "$W"/*',
         'echo \'$W\'; rm -rf "${W:?refusing to delete under an empty path}"/*')
    # ${HOME:?} is not a safety improvement: HOME is always set and non-empty,
    # so it passes and the home directory still goes.
    want("DANGEROUS_VARS are never rewritten", 'rm -rf "$HOME"/x', None)
    # Already asserted by the author; the guard has nothing to add.
    want("an already-guarded operand is untouched", 'rm -rf "${W:?}"/*', None, "allow")
    # Nothing to rewrite means no updatedInput at all.
    want("a literal operand yields no rewrite", "rm -rf /etc/foo", None)
    return fails
```

Wire it in `main()`: `fails += check_rewrite()` and add `+ REWRITE_CHECKS` to `total`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `138/142 passed`, with 4 `FAIL` lines — the four rows expecting a rewritten command get `None`. The three rows expecting `None` pass already.

- [ ] **Step 3: Write minimal implementation**

Extend `emit`:

```python
def emit(decision, reason, updated=None) -> NoReturn:
    out = {"hookEventName": "PreToolUse",
           "permissionDecision": decision,
           "permissionDecisionReason": reason}
    if updated is not None:
        # updatedInput replaces the command before it runs. Only ever a
        # ${VAR:?} rewrite, which expands identically when the variable is set
        # and aborts when it is not -- so a bug here can stop a command early,
        # never broaden what it deletes.
        out["updatedInput"] = {"command": updated}
    print(json.dumps({"hookSpecificOutput": out}))
    sys.exit(0)
```

Add beside the rendering helpers:

```python
GUARD_MSG = "refusing to delete under an empty path"


def rewritable_names(findings):
    """-> names whose expansion the guard can make safe, in first-seen order.

    Only from operands it actually flagged, so an unrelated `$X` elsewhere on
    the line is left alone. DANGEROUS_VARS are excluded because `${HOME:?}`
    proves nothing about HOME -- it is always set and non-empty, so the guard
    passes and the home directory still goes.
    """
    names = []
    for f in findings:
        if f.decision != "ask" or not f.operand:
            continue
        for m in SUBST_NAME_RE.finditer(f.operand):
            n = m.group(1) or m.group(2)
            if n not in DANGEROUS_VARS and n not in names:
                names.append(n)
    return names


def rewrite_guarded(cmd, names):
    """-> cmd with every unquoted $NAME / ${NAME} in `names` made ${NAME:?...}.

    Rewrites EVERY unquoted occurrence rather than only the flagged operand.
    That is simpler than surgical editing and is also the right rule: if the
    command deletes based on $W, it should not run at all when W is empty.
    Behaviour-preserving otherwise -- ${W:?} expands exactly as $W does whenever
    W is set and non-empty.

    Single quotes and quoted heredoc bodies are skipped, because no expansion
    happens in either and a rewrite there would change literal text rather than
    behaviour.
    """
    if not names:
        return cmd
    skip = [m.span() for m in HEREDOC_RE.finditer(cmd)]
    out, i, n, quote = [], 0, len(cmd), None
    while i < n:
        if any(a <= i < b for a, b in skip):
            out.append(cmd[i])
            i += 1
            continue
        c = cmd[i]
        if quote == "'":
            if c == "'":
                quote = None
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
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
        m = SUBST_NAME_RE.match(cmd, i)
        if m and (m.group(1) or m.group(2)) in names:
            out.append("${%s:?%s}" % (m.group(1) or m.group(2), GUARD_MSG))
            i = m.end()
            continue
        out.append(c)
        i += 1
    return "".join(out)
```

In `main`, at the `ask` emit only:

```python
    rewritten = rewrite_guarded(cmd, rewritable_names(blocked))
    emit("ask", render("destructive recursive command needs confirmation", blocked),
         rewritten if rewritten != cmd else None)
```

Leave the `deny` and `allow` emits without `updatedInput`: a denied command never runs, and an allowed one was already asserted safe by its author.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 config/.claude/hooks/test_rm_guard.py`

Expected: `142/142 passed`.

Then confirm the rewritten command is real shell and behaves as claimed — this runs `bash -n` (parse only) and a `printf` probe, never an `rm`:

```bash
bash -n -c 'rm -rf "${W:?refusing to delete under an empty path}"/*' && echo "rewrite parses"
W=/tmp/b bash -c 'printf "set:   [%s]\n" "${W:?msg}"'
bash -c 'printf "empty: [%s]\n" "${W:?msg}"' 2>&1 | tail -1
```

Expected: `rewrite parses`, then `set:   [/tmp/b]`, then a `W: msg` error.

- [ ] **Step 5: Commit**

```bash
git add config/.claude/hooks/rm_guard.py config/.claude/hooks/test_rm_guard.py
git commit -F - <<'EOF'
feat(claude): rewrite an unguarded $VAR to ${VAR:?} before asking

A PreToolUse hook can return updatedInput, which replaces the command
before it runs. The guard has spent its life telling the model to write
"${W:?}"; it can write it itself.

The verdict stays ask, deliberately. ${W:?} proves a variable is
non-empty, not that its target is safe -- W=/etc from the environment
sails through it. What makes the hand-written form an allow is that the
author asserted "I know what W is"; a hook rewriting it asserts nothing.
The rewrite earns the form but not the warrant.

So this does not reduce prompts. It changes what approving one means:
the command you approve can no longer expand to /*.
EOF
```

## Self-Review

**1. Spec coverage.** Rewrite rules 1–5 → Task 2 (`SUBST_NAME_RE` matches only bare and braced forms; single quotes and heredocs skipped; `DANGEROUS_VARS` excluded in `rewritable_names`; message via `GUARD_MSG`; no `updatedInput` when unchanged). Deny-only split → Task 1. The verdict table's one moving row (`W=/` → deny) is Task 1's first test.

**2. Placeholders.** None — every code step carries complete text, every run step names its command and expected output.

**3. Type consistency.** `assigned` is the parameter name in `classify_operand`, `analyse_segment`, `rate`. `rewritable_names` consumes `blocked` (the `ask`/`deny` findings) and returns `[str]`, which `rewrite_guarded` takes as `names`. `emit`'s third parameter is `updated`, a command string or `None`. Counts chain: 131 → 135 (T1) → 142 (T2).
