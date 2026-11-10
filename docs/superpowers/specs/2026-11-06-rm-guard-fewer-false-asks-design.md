# rm guard: fewer false asks, phase 1 (the parser)

The guard is asking about ten times a day, and most of those asks are about
commands it could have read. Replayed over every Bash command in this machine's
transcripts on 2026-11-06 (about 490,000 unique commands), the current
`rm_guard.py` asks on 673. Grouped by the reason it gives:

| bucket                                                        | asks | typical command |
| ------------------------------------------------------------- | ---: | --- |
| `rm -rf` as text in an argument (106 grep, 23 sed, 21 echo/printf) | 150 | `grep -n 'rm -rf' plan.md` |
| path proven, but outside /tmp, /var/tmp, $TMPDIR and cwd      |  144 | `rm -rf ~/temp/x-record/wt-2` |
| `sh -c` / `bash -c`                                           |  121 | `find … -exec sh -c 'echo "$1"' _ {} \;` |
| a variable set on the line, but not first                     |   72 | `cd ~/x && S=/tmp/…; rm -rf $S/race` |
| a loop variable                                               |   65 | `for d in "$HOME/.cache/a" …; do rm -rf "${d:?}"` |
| `.` / `source`                                                |   42 | `. ~/temp/rec/3/env.sh; python3 …` |
| `xargs`, `eval`, `exec`                                       |   30 | `git ls-files -z \| xargs -0 clang-format` |
| everything else                                               |   49 | |

The 144 path asks split as `~/temp` 62, worktrees and checkouts under
`~/projects` 31, `~/.cache` 18, the rest scattered.

Phase 1 makes the parser read more, and closes three holes found along the way.
An LLM reviewer for what is left is phase 2, and gets its own spec once phase 1
has been measured; see the end.

## What does not change

- The deletion target is still an allowlist. Anything unproven asks, a parse
  failure asks, and nothing here relaxes a deny.
- There are two new ways out of an ask, and they are not the same thing.
  **Silence** is only for a line that contains no delete at all, so there is
  nothing to approve and normal permissions (auto mode's classifier) decide.
  **Allow** still needs every operand proven, and is still withdrawn by
  `RISKY_NEIGHBOURS`, because it approves the whole Bash call.
- Something new: two rules read the disk (`.git` markers in fix 6, sourced
  files in fix 3). Until now the guard judged text plus `$HOME`, `$TMPDIR` and
  the cwd. Each disk read has explicit fences, and the limits are listed below.

## Fix 0: a string the guard re-reads may already have been expanded

`ssh h "X=/tmp/a; rm -rf $X/*"` returns **allow** today. The local shell
expands `$X` inside double quotes before ssh sends anything, so if X is unset
here, the far end runs `X=/tmp/a; rm -rf /*`. The guard re-analyses the remote
string after shlex has removed the quotes, so it cannot tell `"…"` from `'…'`,
and the binding inside the string clears an operand the outer shell already
rewrote.

**Rule.** When the guard re-reads a string as a script (the ssh remote
command, and the scripts that fixes 2 and 3 add), and the string contains a `$`
or a backtick, the inner analysis runs **condemn-only**. Inside that string,
bindings, loop word lists and `cd` targets may make a verdict worse, but never
better. A string with no `$` and no backtick cannot have been changed by the
outer shell, and is analysed in full.

This deliberately does not try to prove that a string was single-quoted. That
proof needs a second reading of the source aligned with shlex's, and every
past wrong allow in this file came from two readings disagreeing. The simple
rule costs inner clearing on single-quoted scripts that set their own
variables. Measure that cost on the corpus, and only build the proof if it is
real.

Concretely, condemn-only means two things inside that string. No binding
clears (`rebind_at` is 0). A loop variable may still condemn, and still deny,
but an `allow` it would have produced becomes an `ask`. Positional parameters
need nothing new: `classify_one` already refuses `$1`, `${1:?}` and `$@`,
because `GUARDED_RE` only accepts a name.

## Fix 0b: a quoted heredoc hides the rest of its opening line

`cat <<'EOF' >/dev/null; rm -rf /` with a body and an `EOF` terminator is
**silent** today. `HEREDOC_RE` matches from `<<` through the terminator, so the
`; rm -rf /` on the opening line is masked along with the body.
`strip_data` then finds no guarded word, and `analyse()` returns before it
tokenizes anything. A mistake-plausible spelling is
`cat > x.py <<'EOF' && rm -rf "$OUT"/*`.

**Rule.** `mask_opaque` and `strip_data` replace only the heredoc operator,
the body and the terminator. Whatever follows the delimiter on its opening
line stays in the text, and is read as the code it is. The corpus anonymiser
gets the same change, because it rewrites heredocs with the same regex and
would otherwise drop the opening line's code before the verdict gate compares
anything.

## Fix 1: a mention of `rm -rf` is not a delete

The backstop at the end of `analyse()` asks when `RM_RECURSIVE_RE` matches the
text but no `rm` command was parsed. It is the only thing that catches
`grep x "$(rm -rf /)"` and `` echo `rm -rf /` ``, because substitution bodies
are not analysed. So it can be relaxed only around the shape that is actually
inert.

**Rule.** The backstop goes silent when every token the pattern matches in
meets all of these:

1. It is not the command word of its segment.
2. It contains no command substitution, process substitution or backtick.
3. Its segment's command word is one of `grep`, `egrep`, `fgrep`, `rg`,
   `echo`, `printf`.
4. Its segment has no redirection, except to `/dev/null` or a file-descriptor
   duplication (`2>&1`, `>&2`).
5. Every segment it pipes into has a command word in `head`, `tail`, `wc`,
   `sort`, `uniq`, `cut`, `tr`, `grep`, `egrep`, `fgrep`, `rg`, `cat`, `less`,
   `column`, `nl`.

A match the guard cannot attribute to a token keeps asking. sed and awk are
left out on purpose, because GNU sed's `e` and awk's `system()` run commands,
so the 23 sed mentions keep asking.

Must still ask: `echo "rm -rf /" | bash`, `grep x f > run.sh`,
`grep x "$(rm -rf /)"`, `` echo `rm -rf /` ``, `printf 'rm -rf /' | xargs sh -c`.

## Fix 2: a script handed to a shell is read, not refused

Today `sh -c`, `bash -c` and the other `SHELLS` bail whenever `danger` is set
anywhere on the line, wherever the `-c` appears. Most of the 121 contain no
delete at all, like `docker exec X sh -c "cat …"` or `find -exec sh -c 'echo
"$1"'`, and bail only because something else on the line is destructive.

**Rule.** For `SHELL -c SCRIPT [args…]`, the script is analysed like the ssh
remote command already is, at depth + 1. `-c` may be bundled with other short
flags (`-lc`, `-ec`, `-euc`); an option the guard does not recognise asks, as
it does today. Where the script runs decides how it is judged:

- **The shell is the segment's command word** (`bash -c '…'`): the script runs
  here, against the candidate cwds at that point.
- **The shell is an argument of another command** (`docker exec`,
  `find -exec`, `kubectl exec`, `timeout`): the script may run in another
  filesystem, so it is judged the way the ssh far end is, with no cwd and no
  local safe roots.

`$0` to `$9`, `$@` and `$*` inside the script are bound to arguments the guard
does not track. They behave like a loop variable whose values are not in the
command: they never clear, not even as `${1:?}`. Fix 0 applies too.

**Fix 2b, a hole closed.** `bash <<'EOF'` with `rm -rf "$W"/*` in the body,
and `cat <<'EOF' | sh` with `rm -rf /` in the body, are both **silent** today:
a quoted heredoc's body is masked before analysis, and nothing reads it as a
script. A shell segment with no `-c` and no script operand gets the body
analysed as its script in full, with no condemn-only rule, because a quoted
delimiter means nothing was expanded. That applies when the heredoc
placeholder is in the shell's own segment, or when the shell is piped from a
segment that is exactly `cat` plus the placeholder.

An unquoted heredoc needs nothing new. `mask_opaque` never masks one, so its
body already reaches the tokenizer as lines of code. This makes the guard
stricter, not looser.

## Fix 3: a sourced file is read in place

`. ./env.sh; python3 …` asks today because the sourced file can set variables,
`cd`, or define functions where the guard cannot see. Usually the file is right
there on disk, and is a handful of assignments.

**Rule.** When the **first** command on the line is `. FILE` or `source FILE`,
with exactly one argument and followed by `;`, `&&`, a newline or the end, the
line is analysed as the file's text, then a newline, then the rest of the
line. That only happens when all of these hold:

1. FILE is a plain word: no quotes, no `$`, no backslash, no glob. It
   contains a `/`, because a bare `env.sh` is looked up on `$PATH` first. It
   may start with `~/`. Relative paths resolve against the cwd.
2. It is a regular file after `realpath`, at most 64 KiB, and valid UTF-8.
3. Its text has no `RISKY_NEIGHBOURS` hit, because an allow would approve the
   file's commands too, and no `alias`.
4. The depth limit is shared with ssh and fix 2, so `MAX_SSH_DEPTH` becomes
   `MAX_DEPTH`.
5. The `--no-disk` flag is not set (see Testing).

If any of these fail, it asks as it does today. Findings from the inlined
analysis carry `with FILE read in place` in their label.

No fence is needed against the line writing the file before sourcing it.
The source is the first command, so nothing on the line runs before it.

The file's commands get the full analysis, so an `rm` inside a sourced file is
now judged, where before it was only refused unseen. `cd "$R/wt" || return 1`
needs no special case: `candidate_cwds` already keeps the cwd from before every
`cd` as a candidate, so a `cd` that fails and returns is covered.

## Fix 4: xargs and exec name the command they run

**xargs.** `xargs [opts] CMD ARGS` is analysed as the segment
`CMD ARGS <xargs-input>`. The extra operand is never cleared: it reads as a
value not in the command.

- xargs options come from a table (GNU, plus the BSD subset), split into
  options that take a value (`-a -d -E -I -L -n -P -s`, the long forms, and
  attached spellings like `-n1` and `--max-args=1`) and flags (`-0 -r -t -p -x
  -o`). An unknown option asks.
- With `-I R`, any operand in ARGS containing R becomes `<xargs-input>`.
- With no CMD, xargs runs `echo` and the segment is silent.
- A shell as CMD goes to fix 2, with its positional parameters unknown. An
  `OPAQUE` command as CMD (`xargs eval`) asks.

One fence must not be missed: the rm scan walks every token, so `xargs rm -rf`
reaches it as an rm with **no** operands. Today that is `continue`, so it would
become silent the moment xargs stops bailing. The rm scan must receive the
synthetic operand.

**exec.** `exec` joins `WRAPPERS`, with its options `-a NAME`, `-c` and `-l`.
`exec` followed only by redirections runs nothing. `eval` stays opaque, since
it is 7 asks and has no visible command to name.

`OPAQUE`, the set the bail-out reads, shrinks to `eval`, `source` and `.`.
`_indirection_present` keeps the old five-word set under the name
`INDIRECTION`, so that losing two words from the bail-out does not also loosen
what `proven_bindings` refuses.

## Fix 5: variables the line proves

a. **`$HOME`.** The hook's own `$HOME` goes into the binding map as if the
   line had bound it, but only when all of these hold:
   - the analysis is local, which ssh and docker-style re-reads are not;
   - HOME is absolute and not `/`;
   - HOME is not written anywhere on the line, meaning any mention other than
     a plain read under the `_read_only_mentions` rule.

   From there it gets exactly the treatment a binding gets. It condemns from
   anywhere, and clears only when `may_clear` holds. An operand that resolves
   to exactly `$HOME` is denied by `classify_path`, which keeps `rm -rf
   "$HOME"` a deny. Line bindings still cannot bind HOME, because it stays in
   `DANGEROUS_VARS`.
b. **Tilde.** In a local analysis, a `~` or `~/…` operand is rewritten to
   `${HOME}…` and then resolved by (a). `~user` still asks. A quoted `"~/x"`
   also reaches the guard as `~/x` after shlex. Resolving it as $HOME only
   ever judges a worse path than the `./~/x` that actually runs. Over ssh the
   tilde behaves exactly as it does today.
c. **Loop word lists.** A `for` word resolves like a literal when it is built
   from a proven binding usable at that point (the loop's segment starts before
   `rebind_at`), from `$HOME` or from `~`. `while read` stays unknown.
d. **Where the leading run of bindings may start.** It may now follow `set`
   with flag-only arguments (`set -e`, `set -euo pipefail`), joined by `;`,
   `&&` or a newline. It may also follow `cd` to a literal, but only when the
   `cd` is joined by `;` or a newline. Neither command can write a variable:
   `cd` writes PWD and OLDPWD, which are `DANGEROUS_VARS` and never bindings.

   `cd X && S=/x; rm -rf $S/y` stays an ask, deliberately. If the cd fails,
   `&&` skips the assignment, and the `;` still runs the rm with S unset, as
   `rm -rf /y`. The guard's own advice, `${S:?}`, clears that line.
e. **`cd` to a proven binding places the shell.** `cd $S`, `cd "${S:?}"` and
   `cd "$S/sub"` count as a literal `cd`, provided S is proven and the cd's
   segment starts before `rebind_at`. Those are the same four fences that
   already let a binding clear an operand. This reverses the module
   docstring's "cd "$D"; rm -rf ./build: ask, not allow" on purpose, and that
   paragraph is rewritten to say so and why.
f. **`export` with literal names.** `export NAME=value` and `export NAME`,
   with every argument a literal name and no options, no longer count as a
   nameless rebinding in `_rebinding_possible`. It writes only names that are
   spelled out, which the mention scan already handles. `declare`, `local`,
   `typeset` and `readonly` stay as they are, because `-n` makes a nameref.

## Fix 6: safe roots

a. **`$HOME/temp` and `$HOME/.cache`** become safe roots, locally only, when
   HOME is absolute and not `/`. They follow the rule `/tmp` already has: the
   root itself asks and anything below it clears. A glob is judged by the
   directory before it, so `~/temp/*` asks exactly as `/tmp/*` does today.
   Unlike `/tmp`, they also get a symlink check: the operand's `realpath`
   must lie under the root's `realpath`. Python's `realpath` resolves the
   components that exist and keeps the rest as written.

b. **Inside a git work tree.** For an absolute, normalised operand P, judged
   locally:

   1. Walk up from P's parent. T is the first directory holding a `.git`
      entry. The walk stops before `$HOME`, so T is always strictly below it.
   2. T's `.git` must be a directory containing `HEAD`, or a file whose first
      line starts with `gitdir: `.
   3. P has no `.git` entry of its own. The top of any work tree, including a
      nested worktree or submodule, still asks.
   4. The first component of P below T is not `.git`.
   5. For a glob, nothing new is needed. A glob is judged by its directory, so
      `T/*` is judged as T and asks under rule 3, and `T/build/*` clears.
   6. The realpath of P lies under the realpath of T, and the realpath of T
      lies strictly under the realpath of `$HOME`.

   Any failure gives today's verdict. The rule does not apply over ssh.

## Fix 7: a relative operand is judged by where it lands

Added at the user's request after review. `cd && rm -rf ./Documents` is
**allowed** today, because `classify_path` treats any named relative path as
bounded wherever the shell stands. That is true of its depth, and says nothing
about what the path is. The user would rather see full literal paths, unless a
relative one can be proved safe.

**Rule.**
- A relative operand is resolved against every candidate cwd, as today.
- Each result is then judged exactly as an absolute literal path would be,
  against the same safe roots.
- The roots now come from the session's own cwd (the hook payload's, which
  `candidate_cwds` always lists first) rather than from the candidate. A `cd`
  on the line therefore moves where a path lands, but never makes its own
  target a safe root.
- The existing denies (`/`, depth 1, exactly `$HOME`) and the "whole working
  directory" ask run first, unchanged.

**The session's cwd stops being a root when it is `$HOME` or contains it.**
Otherwise a session started in `~` would clear every path under it, a
`~/Documents` included, and fix 5's `~` resolution would hand it those paths.

**Cost, measured over 135,186 real commands containing a guarded word:** 74
move from allow to ask, and nothing else moves. Most of the 74 are `cd ~/temp/…`
or a `cd` into a worktree, which fixes 6a and 6b clear again.

## Limits accepted

- **Disk is read at check time, and the command runs later.** A line that makes
  a symlink and then deletes through it (`ln -s / repo/l; rm -rf repo/l/etc`)
  is not caught. `/tmp` has the same gap today, because its rule is textual.
- **A sourced file can change** between the check and the run, if some other
  process writes it.
- **A stale `.git`** left by an earlier tool call (`git init ~/projects`)
  widens the git rule to everything under that directory that is not itself a
  repository top. `$HOME` as a repository is excluded.
- **A re-read script containing `$`** loses inner clearing even when it was
  single-quoted (fix 0).
- **Text mentions under sed and awk** keep asking.

## Testing

- **Tests first.** Every fix lands test-first in `test_rm_guard.py`, with the
  corpus shapes above as positive cases and these as must-ask or must-deny
  cases at minimum:
  - all of fix 1's "must still ask" list;
  - `cat <<'EOF' >/dev/null; rm -rf /` with a body and terminator;
  - `ssh h "X=/tmp/a; rm -rf $X/*"` and `bash -c "X=/tmp/a; rm -rf $X/*"`;
  - fix 2b's two heredoc lines;
  - `sh -c 'rm -rf "${1:?}"/*' _ /etc`;
  - `ls | xargs rm -rf` and `xargs -I{} rm -rf {}`;
  - `printf 'cd /\n' > e.sh; . ./e.sh; rm -rf *`, plus a sourced file that
    sets `R=` empty and is followed by `rm -rf "$R"/*`;
  - `HOME=/ ; rm -rf ~/x`;
  - a symlink inside a work tree pointing out of it;
  - `rm -rf repo`, `rm -rf repo/.git` and `rm -rf repo/*`;
  - a `.git` directory without `HEAD`;
  - `$HOME` itself as a repository.
- **Real directories for disk tests.** The git and safe-root tests build their
  repos under a temporary `$HOME` in `/dev/shm`. A home under `/tmp` would sit
  inside a safe root and prove nothing. CI's Docker image has `/dev/shm`.
- **Fixture replay stays hermetic.** `rm_guard.py --no-disk` turns off fix 3's
  inlining and fix 6b's git rule, and swaps 6a's `realpath` for `normpath`.
  It is a command-line flag, not an environment variable. Claude Code runs the
  hook without arguments, so nothing in a session can turn it on. The corpus
  harness passes it on every run. The anonymiser must also keep `temp` and
  `.cache` (path components directly under `$HOME`) unmapped, or
  `--regenerate`'s verdict-preserving gate will refuse, correctly, to write.
- **After each fix, run `test_rm_guard_corpus.py --live`.** List every command
  whose verdict moved, check each ask→allow and ask→silent by hand, then
  `--regenerate`. `tests/test-codex-guard.py` runs too, because Codex uses this
  guard and turns an ask into a refusal.
- **Record the results.** Before and after counts per bucket go in a results
  section at the end of this file.

## Process

The guard gates Claude's own Bash calls, so auto mode refuses to let Claude
edit it in place.

- **Where it's built.** Everything is built in a scratch clone, one commit per
  fix, in the order 0, 0b, 1 to 7. Fix 0 and 0b come first because they are
  live holes. Fix 7 comes last, because it leans on 6's roots to give back
  what it takes.
- **Review before handover.** An independent review hunts only for wrong
  allows and wrong silences. It gets the patch series as its explicit target,
  and creates no worktrees on the real repo.
- **Handover.** The user applies the series with `git -C ~/.dotfiles am`.
- **Docstrings.** The module docstring's "Known gaps" and "Deliberately NOT
  covered" sections are updated in the same commits as the code they describe.

## Results

Measured on 2026-11-09 over every unique Bash command in this machine's
transcripts (552,357), each judged at the cwd it was recorded with, against the
real disk. Base is 21163ee; head is the final series. Nothing was run.

| verdict                         | base  | head  |
| ------------------------------- | ----: | ----: |
| allow                           | 2,456 | 2,493 |
| ask                             |   889 |   721 |
| deny                            |     0 |     2 |
| silent, with an allow withdrawn |    59 |    53 |

Asks by the reason the guard gives:

| reason                                                    | base | head |
| --------------------------------------------------------- | ---: | ---: |
| `rm -rf` as text, no delete parsed                        |  236 |  130 |
| path proven, but outside the safe roots                   |  155 |   81 |
| `sh -c` / `bash -c`                                       |  141 |    9 |
| `.`, `source`, `eval`, `exec`, `xargs`                    |  123 |  107 |
| a loop variable                                           |   64 |   45 |
| unguarded expansion with a suffix                         |   51 |   36 |
| a `cd` the guard cannot follow                            |   29 |   18 |
| a value the line rebuilds, or `${V:?}` in re-read text    |    0 |   75 |
| a command named by a variable                             |    0 |   56 |
| everything else                                           |   90 |  164 |

400 asks went away (262 to allow, 137 to silent, 1 to deny) and 232 arrived
(173 from allow, 59 from silent). Another 51 allows became silent, each beside
a command word the guard cannot read. The two denies are a line that deletes
through `d=/`, and a chain of `cd`s that reaches `/` if they fail.

The new asks are the price of the wrong allows that reviews found in base and in
the first drafts: a `${V:?}` value the guard can read and will not trust
(`W=$HOME`), a relative delete judged where it lands, a command named by a
variable on a line that also deletes, any delete fed by `xargs`. The review
rounds hunted only for wrong allows and wrong silences. The last rounds blocked
only on a verdict looser than base. Whatever was already wrong in base is listed
as a known gap in the module docstring. No deny that base gives was dropped,
false ones included; those are kept by deny-only second readings.

**Changed from the plan, each by the user's decision:**

- **`${VAR:?}` free pass.** It is back for a value the parser cannot read, as
  in base. Values it can read are judged as the literal they are.
- **Proven command words.** A proven word is judged as the literal it holds,
  including a literal assigned later on the line, and the last path component
  of `"$B"`.
- **Unreadable command word.** It leaves the line silent with any allow
  withdrawn, so auto mode's classifier judges it. A deny-shaped argument list
  keeps the ask.
- **Delete text.** It is judged match by match: text in a `grep`, `rg`,
  `echo` or `printf` argument that nothing on the line runs stays silent.
- **zsh.** zoxide's `cd` alias is off in Claude Code's shell, and the AUTO_CD
  rule was dropped, because `zsh -c` never reads stdin as a script.
- **CLAUDE.md rules.** A delete gets its own call, with absolute paths, and a
  search for delete text gets its own call of readers.

`test_rm_guard_corpus.py --regenerate` refuses on drift that predates this
work, so the fixture's reviewed moves went in with `--reverdict`. Fixing the
drift is a follow-up.

## Phase 2, not in this spec: LLM review of what is left

Recorded so the constraints are not rediscovered:

- **Where it runs.** It must run inside `rm_guard.py`. A separate
  `type: "prompt"` hook cannot overrule the guard, because when PreToolUse hooks
  disagree, `ask` takes precedence over `allow`.
- **What it may decide.** It may only turn an "I cannot see" ask into silence,
  so auto mode's classifier still judges the command. It never emits `allow`,
  and never touches a deny or an ask about a path the guard has proven.
- **Treat it as a judgement, not a proof.** The command text is the model's
  input, so a comment in it is a prompt-injection vector. Empty-variable
  expansion is exactly the trap a model is most likely to miss.
- **How it would be called.** Through `claude -p` with no tools, no hooks, no
  session persistence and a JSON schema. `--bare` needs an API key, and
  `--safe-mode` is documented as the subscription-compatible way to skip
  hooks; both need verifying on the installed version.
