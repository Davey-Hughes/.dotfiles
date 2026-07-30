# rm guard: rewrite `$VAR` to `${VAR:?}`, and keep bindings only to deny

> ## WITHDRAWN — the prefix was dropped in `2eaa1f1`
>
> **Only the deny-only binding half of this spec shipped.** The prepended
> validator described below went through four review rounds and produced
> Criticals in every one, across three successive designs:
>
> 1. *Edit the command in place* — its quote-state machine had no `#`-comment
>    handling, so an apostrophe in `# don't touch src` inverted its polarity and
>    it rewrote an unrelated `sed -i 's/$W/OK/'` script. It also laundered its
>    own output: the emitted `rm -rf "${W:?...}"/*` classified as `allow` on the
>    next hop.
> 2. *Prepend a validator* — landed ahead of the assignment it depended on
>    (`W=/tmp/build; rm -rf "$W"/*` exited 127 unconditionally), attached a local
>    validator to a name that only exists over `ssh`, and said nothing in the
>    reason about having modified the command.
> 3. *Prepend a value check too* — refused `rm -rf "${W:?}"/x` when cwd is
>    `$HOME`, though the guard's verdict on that command **and** on the literal
>    it resolves to is `allow`; and attached a validator for the OUTER value of
>    any name bound by `for`/`read`/`local`/a conditional, so a correct approved
>    command could never run, while the prompt claimed in cyan that it was
>    protected.
>
> The common cause is stated here because it is the transferable part: **a
> prepended validator cannot see what the command will do.** Not the cwd after a
> `cd`, not a loop variable's value, not whether `$HOME` is safe — which depends
> on cwd, since `safe_roots()` folds it in. Each fix taught it one more thing it
> cannot know. That is the shape of a wrong design, not a bug with a fix left in
> it.
>
> Two hardening items from the addendum were also reverted, for the same reason
> in miniature: a `danger_hint` narrowing gave back an `allow` on
> `rm \<newline>-rf \<newline>/tmp/x && eval "$C"`, and a `$PWD` check refused
> every `cd`-containing command when the session cwd was `$HOME` while
> protecting nothing, because a prefix reads `$PWD` before the command's own
> `cd`.
>
> **What shipped:** the deny-only binding wiring, and four wrong-`allow` bug
> fixes tracked in the sibling spec. Everything below is kept as the record of
> what was tried and why it failed.


Supersedes the allow path in
`2026-07-29-rm-guard-assignment-tracking-design.md`. That spec's binding scanner
survives, with its role inverted.

**Neither half of this grants a new `allow`.** That is the point of it.

## What became possible

A `PreToolUse` hook can return `updatedInput` under `hookSpecificOutput`, which
replaces the tool's arguments before it runs. Verified live against Claude Code
2.1.220: a hook returning

    {"permissionDecision": "allow",
     "updatedInput": {"command": "echo UPDATEDINPUT_REWRITE_WORKED"}}

for `echo UPDATEDINPUT_PROBE` caused the rewritten command to execute. It works
alongside `allow`, `ask` and `defer`, and the user sees the modified command.

So the guard, which has spent its life telling the model to write `"${W:?}"`,
can write it itself.

## The form is not the warrant

The obvious move -- rewrite, then `allow` -- is wrong, and the reason is worth
stating precisely because it was not obvious.

`${W:?}` proves the variable is **non-empty**. It does not prove the target is
safe:

    W=''      aborts                    <- the incident this guard was built for
    W='/'     would delete [/]/*
    W='/etc'  would delete [/etc]/*

A non-empty `W` can arrive from the environment, where the binding map cannot
see it. So rewriting and allowing would turn today's `ask` on `rm -rf "$W"/*`
into an approved wipe of `/etc`.

The distinction that makes today's rule sound:

- `${W:?}` **written by the author** is an assertion -- *I know what W is*. The
  guard defers to that intent, which is why `rm -rf "${W:?}"/*` allows today.
- `${W:?}` **written by the hook** asserts nothing. Nobody looked at W.

Auto-rewriting earns the form but not the warrant. Granting `allow` on it would
launder an unverified command into the "author vouched for this" category.

**So the rewrite returns `ask`.** What it changes is not whether you are
prompted, but whether approving is safe: the command you approve can no longer
expand to `/*`.

## The split

`${VAR:?}` and the binding map guard different failure modes, and neither
subsumes the other:

- **The rewrite removes the empty-expansion failure mode**, wherever `W` came
  from -- a previous line, the environment, a profile function, `eval` --
  because the shell enforces it at runtime rather than the guard inferring it.
- **The binding map owns `deny`, and only deny.** Nothing else can see that
  `W=/; rm -rf "$W"/*` resolves to a glob under the root.

### Why deny-only is the whole point

The binding map produced six Critical findings across six review rounds, every
one the same shape: *the map claims a binding that is stale or wrong* → a wrong
`allow`. The clearest returns `{'W': '/tmp/build', 'C': 'eval'}` for
`W=/tmp/build; C=eval; $C "$D"; ...` -- proving `C=eval` in its own output while
vouching for `W` anyway.

Deny-only inverts that cost model. A stale binding now produces a **missed
deny**, which is a prompt, not a wipe. All six findings downgrade from Critical
to a cosmetic gap. The map is no longer trusted to say a thing is safe -- only
to say it is not.

## The prefix, and why not an edit

When `classify_operand` returns `ask` because an expansion is unguarded --
`unguarded expansion with a suffix`, or a bare expansion under
`bare_var_ok=False` -- collect the variable names and **prepend** a validator:

    : "${W:?refusing to delete under an empty path}"; <original command>

**Prepend rather than edit.** The first implementation edited the command,
replacing each `$W` in place. That meant locating every expansion in the raw
text, which meant a quote-state machine that had to agree with bash's lexer --
and it did not. It had no `#`-comment handling, so an apostrophe in a comment
inverted its polarity for the rest of the line:

    in:   rm -rf "$W"/build   # don't touch src
          sed -i 's/$W/OK/' notes.txt
    out:  sed -i 's/${W:?...}/OK/' notes.txt

That is an in-place edit of a real file with a different `sed` script, not an
early abort, and the mirror case left the actual `rm` unguarded. Judging a
command with an approximate lexer costs a prompt when it is wrong; writing one
costs whatever the command then does.

A prefix needs no lexer at all. The original is byte-identical after it, `:` does
nothing when every name is set, and the expansion aborts the shell before the
original runs when one is not. Verified behaviour-preserving:

    W=/tmp/b   echo "[$W]"; echo "[${W:?}]"   ->  [/tmp/b] [/tmp/b]
    W=''       echo "[${W:?empty}]"           ->  bash: W: empty

It also cannot launder its own output. Editing produced
`rm -rf "${W:?...}"/*`, which this guard's own allowlist reads as an author
asserting intent and clears on the next hop -- the collapse the `ask` verdict
exists to prevent. With a prefix the operand still says `$W`, so a re-run asks
exactly as the first run did.

Rules:

1. Names come from flagged operands only, so an unrelated `$X` elsewhere on the
   line is untouched.
2. A name must be confirmed present in the raw command. The operand is a shlex
   token, and shlex has already joined `"$W"bar` into `$Wbar` -- reading that
   yields `Wbar`, which is not the variable bash expands. Guarding an
   always-unset phantom would abort every run, so an unconfirmed name is dropped.
   That forgoes protection on such an adjacency; it does not break it.
3. Never guard a `DANGEROUS_VARS` name. `${HOME:?}` is not a safety improvement
   -- `HOME` is always set and non-empty, so it passes and
   `rm -rf "$HOME"/x` still deletes the home directory.
4. Carry a message. The abort is otherwise a bare `parameter null or not set`,
   which says nothing about why a hook touched the command.
5. If there is nothing to guard, emit no `updatedInput` at all.

The verdict stays `ask`.

## Verdict table

| command | today | after | why |
|---|---|---|---|
| `rm -rf "$W"/*` | ask | ask + prefix | approving it is now safe against empty |
| `find "$W" -delete` | ask | ask + prefix | same, via the `bare_var_ok=False` arm |
| `W=/; rm -rf "$W"/*` | ask | **deny** | map resolves it to a glob under `/` |
| `W=/tmp/build; rm -rf "$W"/*` | ask | ask + prefix | map is not consulted for allow |
| `rm -rf "$HOME"` | deny | deny | `DANGEROUS_VARS`, never rewritten |
| `rm -rf "$HOME"/x` | ask | ask | never guarded; `${HOME:?}` implies safety it lacks |
| `rm -rf "${W:?}"/*` | allow | allow | unchanged; author asserted intent |
| `echo '$W'; rm -rf "$W"/*` | ask | ask + prefix | the command body is untouched, so the quoted `$W` stays literal without the guard needing to know it was one |
| `rm -rf /etc/foo` | ask | ask | no expansion to rewrite |
| `rm -rf $(cat list)` | ask | ask | substitution, no name to guard |

Only one verdict changes: `W=/; rm -rf "$W"/*` from `ask` to `deny`. Everything
else keeps its verdict and some commands gain a validator ahead of them.

## Recorded here, and STILL OPEN

`rm -rf "${W:?}"/*` returns `allow` and wipes `/etc` just as readily if
`W=/etc`. That is the same "form is not the warrant" question one level down, and
it predates this work -- the guard treats the author's `:?` as sufficient proof.

The addendum's item 2 proposed keeping the `allow` and attaching a runtime
validator, which would have closed it for free. **That validator was withdrawn**
(see the banner at the top of this file), so the gap is open. Downgrading the
verdict to `ask` instead would make the guard distrust an explicit assertion of
intent and would cost a prompt -- a policy call, not a bug fix.

The same applies to `W=/etc; rm -rf "$W"`, which clears via the bare-expansion
rule. Both are restated in `rm_guard.py`'s module docstring, which is where a
maintainer will actually look.

---

# Addendum: hardening without prompt noise

Prompt noise comes from changing verdicts. The prefix changes the *command*, so
everything below is free. Four items, none of which adds a prompt and one of
which removes some.

## 1. The prefix validates the value, not only emptiness

`${W:?}` catches empty. It says nothing about `W=/etc`. A prepended `case`
catches that at runtime, so approving a flagged command becomes safe against
both. Verdict unchanged.

**The threshold depends on the operand's shape**, and getting this wrong
regresses working commands. The naive form -- always require two path
components in `W` -- refuses `rm -rf "${W:?}"/x` with `W=/tmp`, which is
`rm -rf /tmp/x` and is allowed today. Verified.

A *literal* suffix supplies a bounded component, so the variable only has to be
non-root. A *glob* suffix, or a bare operand, means everything under the
variable goes, so it needs two. This is the same reasoning `classify_operand`
already applies to a literal, where it strips a glob to its prefix directory and
classifies that.

    operand shape   emitted check
    "$W"/literal    case "${W:?empty}" in /|"$HOME"|*/..|*/../*) exit 9;; esac
    "$W"/* or "$W"  ...the same, plus: /*/*) ;; /*) exit 9;;

Checked against the guard's own verdict on the equivalent literal, the runtime
check never refuses what the guard would have *allowed*:

    "$W"/x  W=/tmp   ok        rm -rf /tmp/x   allow
    "$W"/x  W=/etc   ok        rm -rf /etc/x   ask     (backstop stays
                                                       permissive -- approved)
    "$W"/*  W=/tmp   REFUSED   rm -rf /tmp/*   ask
    "$W"    W=/tmp   REFUSED   rm -rf /tmp     ask
    "$W"/x  W=/      REFUSED

Being looser than `ask` is deliberate. The prompt already happened and the user
said yes; the runtime check is a backstop against a value nobody could see, not
a second veto over an approved intent.

## 2. The same prefix on the two blind `allow`s

Both of these clear today with no protection at all:

    rm -rf "${W:?}"/*      the author asserted intent, so the guard defers --
                           but W=/etc still wipes /etc
    W=/etc; rm -rf "$W"    the guard can see the value and clears it anyway,
                           via the bare-expansion heuristic

**Only the first can be fixed this way, and the reason matters.** A prefix runs
at position 0, so for the second it would test the OUTER value of `W`, not the
`/etc` assigned a moment later -- the same fact that forces
`_assigned_as_statement` to drop a statement-assigned name from the prefix
entirely. Hardening it would need a check placed *after* the assignment, which
means editing the command body, which is the design this spec rejects.

So the first gains the value check and keeps its `allow`; the second stays a
known gap, pinned by a test row so it cannot change silently.

This requires emitting `updatedInput` on an `allow`, which is supported and was
the form originally verified live.

## 3. The stale-`cwd` gap

`cd /; rm -rf build` clears, because `build` is judged against the *hook's* cwd
rather than the shell's after the `cd`. Prepending
`case "$PWD" in /|"$HOME") exit 9;; esac` closes it at runtime, for any command
whose cleared operand was relative. Zero prompts.

## 4. `danger_hint` needs proximity -- this REMOVES prompts

It pairs a command name with a destructive flag anywhere in the text, in any
order and across segments, so `git add a b && echo "clean"` reads as
`git clean`. That false pairing is what turned an unrelated parse failure into a
prompt at the very start of this work. Requiring the pair within one segment
only narrows a heuristic that gates conservative bail-outs, so it cannot weaken
a real verdict.
