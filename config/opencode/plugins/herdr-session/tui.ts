// OpenCode CLI plugin: inside herdr, report the session on screen as the pane
// token $session, for herdr's sidebar and tab names. The TUI runs in the pane,
// so HERDR_PANE_ID is the right pane; the server, a shared daemon, would not
// know it. See name.ts.

import { Plugin } from "@opencode/plugin/tui"
import { createHerdrSession } from "./name.ts"

export default Plugin.define({
  id: "davey.herdr-session",
  setup(context) {
    const socket = process.env.HERDR_SOCKET_PATH
    const pane = process.env.HERDR_PANE_ID
    if (process.env.HERDR_ENV !== "1" || !socket || !pane) return

    const session = context.data.session
    const herdr = createHerdrSession({
      socket,
      pane,
      data: {
        route() {
          const route = context.ui.router.current()
          return route.type === "session" ? route.sessionID : undefined
        },
        root: (id) => session.root(id),
        title: (id) => session.get(id)?.title,
      },
    })

    // A poll rather than events: it is a lookup and a string compare, and it
    // catches a switch of session too, which sends no event. herdr-status.sh
    // only looks once a second anyway.
    const poll = setInterval(() => void herdr.refresh(), 1000)
    void herdr.refresh()

    return () => {
      clearInterval(poll)
      void herdr.close()
    }
  },
})
