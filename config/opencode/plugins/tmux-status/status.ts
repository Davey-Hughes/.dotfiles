// Paint an OpenCode pane's tmux tab with the shared Claude/Codex renderer,
// ~/.config/.claude/hooks/tmux-status.sh.
//
// Claude Code and Codex hooks fire per event and translate one event into one
// renderer call. OpenCode's TUI already keeps the session state, so this
// derives the tab's state from it instead and calls the renderer only when
// that state changes. Rendering ends up in the pane options ~/.tmux.conf reads.
//
// Kept free of OpenCode imports so tests/test-opencode-tmux.py can drive it
// under node; tui.ts adapts the plugin context to `Data`.

import { spawn, spawnSync } from "node:child_process"

export type Data = {
  // The session the TUI is showing, or nothing on the home screen.
  route(): string | undefined
  root(id: string): string | undefined
  // Every session in the tree under `id`. May or may not include `id`.
  family(id: string): string[]
  running(id: string): boolean
  // A prompt queued behind the current turn.
  queued(id: string): boolean
  // A permission request or a form (a question dialog) is up.
  waiting(id: string): boolean
  failed(id: string): boolean
  // The text of the session's last assistant message.
  lastReply(id: string): Promise<string>
}

type Mode = "working" | "idle" | "blocked" | "failed"
type Shown = { session: string; mode: Mode; subs: number }

// Session ids end up in a shell command on restore; see resurrect-opencode.sh.
export const SESSION_ID = /^ses_[A-Za-z0-9]+$/

export function createTmuxStatus(options: { pane: string; renderer: string; data: Data }) {
  const { pane, renderer, data } = options
  const env = { ...process.env, TMUX_PANE: pane, TMUX_STATUS_OPENCODE: "1" }

  const exec = (command: string, args: string[], input?: string) =>
    new Promise<void>((resolve) => {
      const child = spawn(command, args, {
        env,
        stdio: [input === undefined ? "ignore" : "pipe", "ignore", "ignore"],
      })
      child.on("error", () => resolve())
      child.on("close", () => resolve())
      if (input !== undefined) child.stdin?.end(input)
    })
  const render = (args: string[], input?: string) => exec("bash", [renderer, ...args], input)

  let shown: Shown | undefined
  let started = false
  let closed = false

  async function apply() {
    const id = data.route()
    if (!id) {
      // `off` also drops @oc_session, so the save hook leaves the pane alone.
      if (shown || !started) await render(["off"])
      shown = undefined
      started = true
      return
    }

    // A subagent's session is part of its root's turn; follow the root.
    const root = data.root(id) ?? id
    const family = [...new Set([root, ...data.family(root)])]
    const subs = family.filter((s) => s !== root && data.running(s)).length
    const mode: Mode = family.some((s) => data.waiting(s))
      ? "blocked"
      : data.running(root) || data.queued(root)
        ? "working"
        : data.failed(root)
          ? "failed"
          : "idle"

    const fresh = shown?.session !== root
    if (!fresh && shown!.mode === mode && shown!.subs === subs) return

    if (fresh) {
      await render(["reset"])
      if (SESSION_ID.test(root)) await exec("tmux", ["set-option", "-p", "-t", pane, "@oc_session", root])
    }
    const subsChanged = fresh ? subs > 0 : subs !== shown!.subs
    if (subsChanged) await render(["subagent", `=${subs}`])
    // The renderer repaints from its own counters on a subagent change, which
    // knows nothing of a dialog or a failed turn. Put those back.
    if (fresh || mode !== shown!.mode || (subsChanged && (mode === "blocked" || mode === "failed"))) {
      if (mode === "idle") {
        // The renderer checks whether the turn ended on a question.
        const reply = await data.lastReply(root).catch(() => "")
        await render(["idle"], JSON.stringify({ last_assistant_message: reply }))
      } else {
        await render([mode])
      }
    }
    shown = { session: root, mode, subs }
    started = true
  }

  let loop: Promise<void> | undefined
  let again = false
  function refresh(): Promise<void> {
    if (closed) return Promise.resolve()
    if (loop) {
      again = true
      return loop
    }
    loop = (async () => {
      do {
        again = false
        await apply().catch(() => {})
      } while (again && !closed)
    })().finally(() => {
      loop = undefined
    })
    return loop
  }

  return {
    refresh,
    idle: () => loop ?? Promise.resolve(),
    async close() {
      closed = true
      await loop
      await render(["off"])
    },
    // From a process exit handler, where nothing asynchronous gets to run.
    closeSync() {
      closed = true
      spawnSync("bash", [renderer, "off"], { env, stdio: "ignore", timeout: 2000 })
    },
  }
}
