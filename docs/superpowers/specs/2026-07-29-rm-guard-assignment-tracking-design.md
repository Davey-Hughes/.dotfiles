# rm guard: clear an operand whose variable is assigned in the same command

## Problem

`rm_guard.py` classifies an operand by what it can prove statically. An
unguarded expansion with a suffix always asks:

    rm -rf "$W"/*    ->  ask ("if it is empty this becomes a path under /")

That verdict is right when `W` comes from anywhere the guard cannot see. It is
needless when the command assigns `W` a literal two words earlier:

    W=/tmp/build; rm -rf "$W"/*

The value is sitting in the command string. The guard should read it.

## Scope, and why it is a hard ceiling

The hook payload carries one command string plus `cwd`. Nothing else.

Claude Code's Bash tool does not persist shell state between calls -- each call
runs in a fresh shell. So an assignment in a *previous* tool call is not merely
invisible to the guard, it is genuinely not set:

    call 1:  W=/tmp/build
    call 2:  rm -rf "$W"/*      ->  W is empty  ->  rm -rf /*

That is the catastrophe this guard exists to prevent, so cross-call tracking is
not an unimplemented feature. It is wrong. `transcript_path` is in the payload
and could be read; it must not be.

The only tractable form is assignment and use in the same command string.

## Design

### Where bindings come from

Bindings are read from the **token stream**, walking `segment_bounds` -- the same
view every other rule in this file uses.

That was not possible when this spec was first written. `shlex` consumed a
newline as whitespace, so

    W=/tmp/build\nrm -rf "$W"/*      (sequential -- the binding holds)
    W=/tmp/build rm -rf "$W"/*       (env prefix -- it does NOT, because the
                                      shell expands $W from the outer scope
                                      before the assignment takes effect)

tokenized to the same list, and only the first proves anything. Commit `e3c8e72`
made `mask_opaque` normalise unquoted newlines to `;`, which is what lets the
sequential form read as two statements and the env-prefix form as one -- so the
regex below sees `W=/tmp/build` terminated by a `;` in the first case, and a
value running on into `rm` in the second.

`proven_bindings(cmd) -> {name: literal}`, read from the masked **source** with
one anchored regex, applied repeatedly from position 0:

    [ \t]*(NAME)=(VALUE)[ \t]*(;|&&|\Z)

where `VALUE` excludes every character that would need a quote, an escape or an
expansion to survive: whitespace, `'`, `"`, `\`, `;`, `&`, `|`, `(`, `)`, `<`,
`>`, `$`, a backtick, and `SENTINEL`. Each match binds a name (last wins, as the
shell does) and advances the cursor; the first non-match ends the run, and the
remaining text is scanned for other mentions.

**Why one parser, and not the token stream.** A token-level scan looked simpler
and cost three consecutive holes, each a wrong `allow` on `rm -rf /*`:

1. shlex performs quote removal, but bash recognises an assignment **before**
   it. So `'W=/x'`, `"W=/x"`, `"W"=/x` and `'W'=/x` are all command names whose
   lookup fails, leaving `W` unset -- while arriving as the same token a real
   `W=/x` produces.
2. Recovering that from the source by asking whether `NAME=` appears unquoted
   *anywhere* let a genuine `W=/tmp/ok` vouch for a later quoted `'W=/tmp/evil'`
   that assigns nothing.
3. Making it positional required a second parser kept in lockstep with shlex,
   and a scanner blind to backslashes split `A=x\;W=/tmp/ok` into two words
   where shlex made one -- shifting every later index so one segment's source
   vouched for another's.

All three are divergences between two readings of the same string. One reading
cannot diverge from itself. The restricted value charset is what makes a single
reading sufficient: it is exactly the set of values on which the two readings
could never have disagreed.

The cost is real and is paid in prompts, never in wrong verdicts. These stop
binding and fall through to `ask`:

    W="/tmp/my build"     W='/tmp/build'     W=/tmp/"build"
    W=/tmp/my\ build      A=x\;W=/tmp/ok

A value that needed quoting is a value this cannot vouch for. That is the
allowlist rule applied to the scanner itself.

Separator handling falls out of the regex rather than needing `toks[stop]`:
only `;`, `&&` and end-of-input continue the run. `|` and `&` are outside the
value charset and terminate the match, which is correct -- a pipeline segment's
assignment never escapes its subshell, and `A=x || rm ...` runs the rm only if
the assignment failed.

Repeated names take the last value: `A=/tmp; A=/; rm -rf "$A"/*` binds `/`, and
denies. Last-wins is both what the shell does and the conservative direction.

Because the run must start at the first segment, `echo hi; W=/tmp/x; rm -rf
"$W"/*` yields no bindings. That is deliberate. Proving that `echo hi` cannot
touch `W` means reasoning about arbitrary commands, which is the thing this
guard refuses to do.

Tokenizing moves into a `tokenize(cmd)` helper. It was introduced so the binding
scan and its tests could share `analyse`'s token stream; the single-parser
redesign removed that need, and the helper now has one caller. It stays because
one construction site for the `mask_opaque` + `shlex` incantation is worth
keeping regardless. It raises `ValueError`, which `analyse` already handles.

### What disqualifies a binding

Two filters, applied to the map before it is used.

**Any other mention of the name.** Scan the remainder tokens. If the name
appears in any of them as anything other than `$NAME` or `${NAME}`, drop that
binding. One blunt rule covers every rebinding path without cataloguing shell
builtins:

    unset W        read W         W+=/x          declare W
    W=other        export W=/x    ((W=1))        for W in ...
    eval "W=/"     (W=/)          local W

All of them mention `W`. The rule is intentionally over-broad -- it also drops
`echo "$W" ; rm -rf "$W"/*`-style harmless mentions, which costs a prompt.

**`DANGEROUS_VARS`.** A name in that set is never substituted, whatever the
command assigns to it:

    HOME=/tmp/fake; rm -rf "$HOME"      ->  deny

The deny list stays absolute. A same-line reassignment of `HOME`, `PWD`,
`OLDPWD`, `ROOT`, `USER`, or `TMPDIR` next to a recursive delete is either
clever or confused, and one unexplained exception in the deny list costs more
than the prompt does.

### How a binding is used

`classify_operand` gains an `assigned=None` parameter, threaded through
`analyse` -> `analyse_segment` -> `rate`. Three call sites need it: `rate`, the
`git clean` branch that calls `classify_operand` directly, and the `rm` loop in
`analyse`. Before the existing expansion checks, `classify_operand` replaces
each `$NAME` and `${NAME}` for a bound name with that name's literal. Then:

- If no expansion survives the substitution, classify the resulting literal via
  the existing `classify_path` / glob logic.
- If any expansion survives, fall through to today's logic unchanged. A
  partially-resolved operand is not resolved.

Escalation comes free, because the substituted text goes through the same
classifier as any literal:

    W=/tmp/build; rm -rf "$W"/*   ->  allow  (glob under safe root /tmp)
    W=/;          rm -rf "$W"/*   ->  deny   (glob directly under root)
    W=/etc;       rm -rf "$W"/*   ->  ask    (outside safe roots)

The payoff extends past `rm`. Every non-rm command is classified with
`bare_var_ok=False`, because `find $W -delete` with `W` unset becomes
`find -delete`, which is `rm -rf .`. A proven binding clears that whole class:

    W=/tmp/x; find "$W" -delete   ->  allow

### ssh

Bindings are **not** passed into the `analyse(remote, "", depth + 1)`
recursion. The local shell expands a double-quoted remote command before `ssh`
sees it, and `shlex` has already stripped the quotes by then, so the guard
cannot tell `ssh h "rm -rf $W/*"` (expanded locally) from
`ssh h 'rm -rf $W/*'` (expanded on the far end). Withholding the bindings makes
the remote operand unproven, which is the safe direction.

### Rendering

`unknown_spans` must not paint a substituted name `UNKNOWN` (magenta). Magenta
means "the guard cannot know this value"; once a binding is proven, it can. The
echoed command still shows what was written -- `rm -rf $W/*` -- and the reason
carries the resolved value:

    $W/*    W is assigned /tmp/build in this command, so this reads as
            /tmp/build/*, a glob under safe root /tmp

## Cost model

This is the one change that can turn a gap in the guard into an `allow` rather
than an `ask`. Every other unhandled case in `rm_guard.py` lands on `ask`, so a
bug there costs a prompt; a bug in binding discovery costs a filesystem.

Three structural properties keep that bounded, and they are the reason the
design is shaped this way rather than as general dataflow:

1. Bindings come only from a leading run of assignments, so they are
   unconditional. A heredoc body cannot supply one because `mask_opaque` has
   already reduced it to a sentinel, which the value charset excludes; a
   subshell cannot because `|` and `&` end the run; a conditional branch cannot
   because `if x` does not match `NAME=`.
2. The value charset excludes `$`, backtick, sentinel, quotes and backslash, so
   substituting a value cannot introduce a new expansion -- and no second
   reading of the source exists to disagree with the first.
3. Any other mention of the name disqualifies it.

## Not doing

**Documenting this in `CLAUDE.md`.** `"${VAR:?}"` is proven at runtime and holds
even if the guard's inference is wrong; a same-string literal assignment is
proven only by static inference. Telling Claude the weaker form is acceptable
invites reaching for it. `CLAUDE.md` keeps pushing the colon form; the guard
just stops prompting on the provable case.

**Resolving non-literal values.** `W="$PWD/build"` could be resolved against the
payload's `cwd`, and `W=$(mktemp -d)` cannot be resolved at all. Both fall
through to today's logic.

**The `CLAUDE.md` scope gap.** The entry names `rclone sync` while the guard
covers `purge`, `delete`, `deletefile`, `rmdir`, `rmdirs`, `move`, and
`cleanup`, and it names `find -exec rm` while the guard covers
`-exec`/`-execdir`/`-ok`/`-okdir` against `rm`/`rmdir`/`unlink`/`shred`/
`trash`/`truncate`/`dd`. Real, but independent of this change and landing as its
own commit.

## Tests

Added to `test_rm_guard.py`, which drives the guard through its CLI and asserts
the verdict. `CWD` is the existing synthetic `/home/u/project`.

| command | verdict | what it pins |
|---|---|---|
| `W=/tmp/build; rm -rf "$W"/*` | allow | the base case |
| `W=/tmp/build\nrm -rf "$W"/*` | allow | newline form; tokenized like an env prefix before `e3c8e72` |
| `W=/tmp/build && rm -rf "$W"/*` | allow | `&&` terminates a statement |
| `A=/tmp; B=x; rm -rf "$A/$B"/*` | allow | two bindings in one operand |
| `W=/tmp/x; find "$W" -delete` | allow | clears the `bare_var_ok=False` class |
| `W=/; rm -rf "$W"/*` | deny | escalation via the substituted literal |
| `W=/tmp/x; W=/; rm -rf "$W"/*` | deny | last-wins |
| `HOME=/tmp/fake; rm -rf "$HOME"` | deny | `DANGEROUS_VARS` stays absolute. The bare form, because `BARE_VAR_RE` only matches a whole-token expansion -- `"$HOME"/x` asks today, with or without this change |
| `W=/etc; rm -rf "$W"/*` | ask | substitution is not a blanket pass |
| `W=/tmp/x rm -rf "$W"/*` | ask | env prefix proves nothing |
| `W=/tmp/x; unset W; rm -rf "$W"/*` | ask | other-mention rule |
| `echo hi; W=/tmp/x; rm -rf "$W"/*` | ask | run must start at offset 0 |
| `W=/tmp/x \| cat; rm -rf "$W"/*` | ask | `\|` ends the run (subshell) |
| `W=$HOME; rm -rf "$W"/*` | ask | value is not literal |
| `W=/tmp/x; ssh h 'rm -rf $W/*'` | ask | bindings do not cross ssh |
| heredoc body containing `W=/tmp/safe`, then `rm -rf "$W"/*` | ask | a body cannot bind: `mask_opaque` reduced it to one sentinel token |

## Build order

1. `leading_bindings` plus the two disqualifying filters, with unit coverage.
2. `assigned` parameter through `classify_operand` / `analyse_segment` /
   `analyse`, and the ssh withholding.
3. Rendering: `unknown_spans` suppression and the reason text.
4. The verdict table above.
