// Report the OpenCode session on screen to herdr as the pane token $session.
// ~/.config/herdr/config.toml shows it on OpenCode's sidebar row, and
// ~/.tmux/herdr-status.sh names the herdr tab after it.
//
// The terminal title already carries it, but as "OC | <title>" cut to 40
// characters, and herdr cannot strip that. So the title goes over herdr's
// socket instead, whole, and only when it changes.
//
// Kept free of OpenCode imports so tests/test-opencode-herdr.py can drive it
// under node; tui.ts adapts the plugin context to `Data`.

import net from "node:net"

export type Data = {
  // The session the TUI is showing, or nothing on the home screen.
  route(): string | undefined
  root(id: string): string | undefined
  title(id: string): string | undefined
}

// Who reported the token, as herdr records it.
export const SOURCE = "dotfiles:opencode"

// The title OpenCode gives a session until it has a real one, and shows as
// plain "OpenCode" in the terminal title.
const PLACEHOLDER = /^(New session|Child session) - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/

// The name of the conversation on screen, or "" for none.
export function sessionName(data: Data): string {
  const id = data.route()
  if (!id) return ""
  // A subagent's session is part of its root's conversation: name the root,
  // so opening a subagent does not rename the tab.
  const title = (data.title(data.root(id) ?? id) ?? "").replace(/\s+/g, " ").trim()
  return PLACEHOLDER.test(title) ? "" : title
}

// One pane.report_metadata request. An empty name clears the token. Resolves
// true once herdr answers, false if it cannot be reached in time.
export function report(socket: string, pane: string, name: string): Promise<boolean> {
  const request = {
    id: `${SOURCE}:${Date.now()}`,
    method: "pane.report_metadata",
    params: { pane_id: pane, source: SOURCE, tokens: { session: name } },
  }
  return new Promise((resolve) => {
    let settled = false
    const settle = (delivered: boolean) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      client.destroy()
      resolve(delivered)
    }
    const client = net.createConnection(socket, () => client.write(`${JSON.stringify(request)}\n`))
    const timer = setTimeout(() => settle(false), 500)
    timer.unref?.()
    client.on("data", () => settle(true))
    client.on("error", () => settle(false))
    client.on("close", () => settle(false))
  })
}

export function createHerdrSession(options: { socket: string; pane: string; data: Data }) {
  const { socket, pane, data } = options
  // What herdr was last told; a failed report leaves it, so the next refresh
  // tries again.
  let sent = ""
  let closed = false

  async function apply(name: string) {
    if (name === sent) return
    if (await report(socket, pane, name)) sent = name
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
        await apply(sessionName(data)).catch(() => {})
      } while (again && !closed)
    })().finally(() => {
      loop = undefined
    })
    return loop
  }

  return {
    refresh,
    async close() {
      closed = true
      await loop
      await apply("").catch(() => {})
    },
  }
}
