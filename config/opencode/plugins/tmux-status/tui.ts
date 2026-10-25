// OpenCode CLI plugin: tmux activity glyph, prefix-u and resurrect for
// OpenCode panes. The TUI runs in the pane, so TMUX_PANE is the right pane;
// the server, which runs the agent, is a shared daemon and would not know it.
// See status.ts for the state, and ~/.tmux.conf for the rest of the wiring.

import { Plugin } from "@opencode/plugin/tui"
import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { createTmuxStatus } from "./status.ts"

// Events that only stream output. Everything else may change the state.
const NOISE = /delta|progress|streamed|\.input\./

export default Plugin.define({
  id: "davey.tmux-status",
  setup(context) {
    const pane = process.env.TMUX_PANE
    const config = process.env.XDG_CONFIG_HOME || join(homedir(), ".config")
    const renderer = join(config, ".claude/hooks/tmux-status.sh")
    if (!pane || !existsSync(renderer)) return

    const session = context.data.session
    const status = createTmuxStatus({
      pane,
      renderer,
      data: {
        route() {
          const route = context.ui.router.current()
          return route.type === "session" ? route.sessionID : undefined
        },
        root: (id) => session.root(id),
        family: (id) => session.family(id) ?? [],
        running: (id) => session.status(id) === "running",
        queued: (id) => (session.pending.list(id) ?? []).some((item) => item.type !== "synthetic"),
        waiting: (id) =>
          (session.permission.list(id)?.length ?? 0) > 0 || (session.form.list(id)?.length ?? 0) > 0,
        failed: (id) => session.get(id)?.outcome === "failed",
        async lastReply(id) {
          await session.message.sync(id)
          let last: any
          for (const message of session.message.list(id) ?? []) {
            if (message?.type !== "assistant") continue
            if (!last || (message.time?.created ?? 0) >= (last.time?.created ?? 0)) last = message
          }
          return (last?.content ?? [])
            .filter((part: any) => part?.type === "text")
            .map((part: any) => part.text ?? "")
            .join("\n")
        },
      },
    })

    // Events cover state changes; the poll catches switching sessions or tabs
    // with nothing running, which sends no event.
    let timer: ReturnType<typeof setTimeout> | undefined
    const soon = () => {
      timer ??= setTimeout(() => {
        timer = undefined
        void status.refresh()
      }, 100)
    }
    const stop = context.data.listen(({ details }: any) => {
      if (!NOISE.test(details?.type ?? "")) soon()
    })
    const poll = setInterval(() => void status.refresh(), 1000)
    const exit = () => status.closeSync()
    process.once("exit", exit)
    void status.refresh()

    return () => {
      stop()
      clearInterval(poll)
      if (timer) clearTimeout(timer)
      process.off("exit", exit)
      void status.close()
    }
  },
})
