// tmux-status -- OpenClaw activity in the tmux tab, the way Claude Code and
// Codex already show it.
//
// The renderer is the shared ~/.config/.claude/hooks/tmux-status.sh. Claude Code
// and Codex call it from hooks that run inside the pane. OpenClaw's agent runs in
// the gateway, so its hooks see a session key and no pane. This plugin closes
// that gap: on each event it asks tmux-session.sh which panes' TUI footers show
// that session, then runs the renderer once per pane with TMUX_PANE set:
//
//   model_call_started, before_tool_call       working
//   before_tool_call ask_user / secrets request blocked  (after_tool_call: working)
//   agent_end                                  idle, or failed on a real error;
//                                              the final text goes in so a turn
//                                              that ends on a question paints red
//   subagent_spawned / subagent_ended          subagent =<live children>
//   session_start / session_end                reset / off
//
// It also keeps each pane's @cc_name on the session label (`label`), which names
// the window, and it resets a pane whose TUI has switched to another session.
//
// Rendering is cosmetic, so it must never slow or break a turn. Handlers return
// immediately; the work goes on one serial queue, so a turn's events still land
// in order. Every child process has a timeout, and every failure is swallowed.
// Native plugins run in the gateway process. See README.md (OpenClaw) for setup.

import { spawn } from "node:child_process";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SESSIONS = join(HERE, "tmux-session.sh");
const CONFIG_HOME = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
const DEFAULT_RENDERER = join(CONFIG_HOME, ".claude/hooks/tmux-status.sh");

const CHILD_TIMEOUT_MS = 5000;
const RESOLVE_TTL_MS = 1000; // a burst of tool calls shares one footer read
const REPEAT_WORKING_MS = 2000; // `working` while already working does nothing
const START_RETRY_MS = 600; // the TUI redraws its footer after session_start
const MAX_QUEUED = 64; // past this, drop new `working` paints

const DIALOG_TOOLS = (name, params) =>
  name === "ask_user" || (name === "secrets" && params?.action === "request");
// An aborted run (Esc, /stop) is the user's own doing, not a failure to flag.
const USER_ABORT = /abort|cancel|interrupt|stopped/i;

function run(cmd, args, { env, input = "" } = {}) {
  return new Promise((resolve) => {
    let out = "";
    let child;
    try {
      child = spawn(cmd, args, {
        env: env ?? process.env,
        stdio: ["pipe", "pipe", "ignore"],
        timeout: CHILD_TIMEOUT_MS,
        killSignal: "SIGKILL",
      });
    } catch {
      resolve("");
      return;
    }
    child.stdout.on("data", (chunk) => {
      if (out.length < 65536) out += chunk;
    });
    child.on("error", () => resolve(""));
    child.on("close", () => resolve(out));
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

function lastAssistantText(messages) {
  if (!Array.isArray(messages)) return "";
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.role !== "assistant") continue;
    if (typeof m.content === "string") return m.content;
    if (Array.isArray(m.content)) {
      const text = m.content
        .filter((c) => c?.type === "text" && typeof c.text === "string")
        .map((c) => c.text)
        .join("\n");
      if (text) return text;
    }
  }
  return "";
}

// Exported for tests/test-openclaw-tmux.py, which drives it against a fake tmux.
export function createTmuxStatus({
  renderer = DEFAULT_RENDERER,
  sessions = SESSIONS,
  env = process.env,
  resolveTtlMs = RESOLVE_TTL_MS,
  repeatWorkingMs = REPEAT_WORKING_MS,
  startRetryMs = START_RETRY_MS,
} = {}) {
  let queue = Promise.resolve();
  let queued = 0;
  let resolved = { at: 0, panes: [] };
  const shown = new Map(); // pane -> session key it showed last time we looked
  const labelled = new Map(); // pane -> label last sent
  const lastVerb = new Map(); // pane -> { verb, at }
  const children = new Map(); // requester session key -> Set of child keys

  const norm = (key) => (typeof key === "string" ? key.trim().toLowerCase() : "");

  function enqueue(job, { droppable = false } = {}) {
    if (droppable && queued >= MAX_QUEUED) return queue;
    queued++;
    queue = queue
      .then(job)
      .catch(() => {})
      .finally(() => {
        queued--;
      });
    return queue;
  }

  async function render(pane, verb, args = [], input = "") {
    const now = Date.now();
    const last = lastVerb.get(pane);
    if (verb === "working" && last?.verb === "working" && now - last.at < repeatWorkingMs) return;
    lastVerb.set(pane, { verb, at: now });
    if (verb === "off") labelled.delete(pane);
    await run("bash", [renderer, verb, ...args], {
      env: { ...env, TMUX_PANE: pane, TMUX_STATUS_OPENCLAW: "1" },
      input,
    });
  }

  // Which session each OpenClaw pane shows, plus the upkeep that only needs a
  // fresh look: window names, and a clean slate for a pane that changed session.
  async function resolve(fresh = false) {
    if (!fresh && Date.now() - resolved.at < resolveTtlMs) return resolved.panes;
    const panes = [];
    for (const line of (await run("bash", [sessions, "panes"], { env })).split("\n")) {
      const [pane, key, label = ""] = line.split("\t");
      if (pane && key) panes.push({ pane, key: norm(key), label });
    }
    resolved = { at: Date.now(), panes };
    const live = new Set(panes.map((p) => p.pane));
    for (const map of [shown, labelled, lastVerb]) {
      for (const pane of map.keys()) if (!live.has(pane)) map.delete(pane);
    }
    for (const { pane, key, label } of panes) {
      const before = shown.get(pane);
      shown.set(pane, key);
      if (before !== undefined && before !== key) {
        // The TUI moved to another session. Whatever this tab showed belonged
        // to the old one; start from idle, and let the next event say more.
        await render(pane, "off");
        await render(pane, "reset");
        const subs = children.get(key)?.size ?? 0;
        if (subs) await render(pane, "subagent", [`=${subs}`]);
      }
      if (labelled.get(pane) !== label) {
        labelled.set(pane, label);
        await render(pane, "label", label ? [label] : []);
      }
    }
    return panes;
  }

  async function paintNow(sessionKey, verb, args, input, fresh) {
    const key = norm(sessionKey);
    if (!key) return 0;
    const panes = (await resolve(fresh)).filter((p) => p.key === key);
    for (const { pane } of panes) await render(pane, verb, args, input);
    return panes.length;
  }

  function paint(sessionKey, verb, { args = [], input = "", fresh = false, retry = false } = {}) {
    if (!norm(sessionKey)) return;
    enqueue(
      async () => {
        const hit = await paintNow(sessionKey, verb, args, input, fresh);
        if (!hit && retry) {
          setTimeout(() => enqueue(() => paintNow(sessionKey, verb, args, input, true)), startRetryMs);
        }
      },
      { droppable: verb === "working" },
    );
  }

  function countChildren(requester, child, delta) {
    const key = norm(requester);
    if (!key || !child) return;
    const set = children.get(key) ?? new Set();
    if (delta > 0) set.add(norm(child));
    else set.delete(norm(child));
    if (set.size) children.set(key, set);
    else children.delete(key);
    paint(key, "subagent", { args: [`=${set.size}`] });
  }

  const handlers = {
    model_call_started(event, ctx) {
      paint(ctx?.sessionKey ?? event?.sessionKey, "working");
    },
    before_tool_call(event, ctx) {
      const name = event?.toolName ?? ctx?.toolName;
      paint(ctx?.sessionKey, DIALOG_TOOLS(name, event?.params) ? "blocked" : "working");
    },
    after_tool_call(event, ctx) {
      const name = event?.toolName ?? ctx?.toolName;
      // PostToolUse's job in the Claude hooks: the dialog was answered.
      if (DIALOG_TOOLS(name, event?.params)) paint(ctx?.sessionKey, "working");
    },
    agent_end(event, ctx) {
      const error = typeof event?.error === "string" ? event.error : "";
      if (event?.success === false && error && !USER_ABORT.test(error)) {
        paint(ctx?.sessionKey, "failed");
        return;
      }
      const input = JSON.stringify({
        hook_event_name: "Stop",
        last_assistant_message: lastAssistantText(event?.messages),
      });
      paint(ctx?.sessionKey, "idle", { input });
    },
    subagent_spawned(event, ctx) {
      countChildren(ctx?.requesterSessionKey, ctx?.childSessionKey ?? event?.childSessionKey, +1);
    },
    subagent_ended(event, ctx) {
      countChildren(ctx?.requesterSessionKey, ctx?.childSessionKey ?? event?.targetSessionKey, -1);
    },
    session_start(event, ctx) {
      paint(event?.sessionKey ?? ctx?.sessionKey, "reset", { fresh: true, retry: true });
    },
    session_end(event, ctx) {
      if (event?.reason === "compaction") return;
      const key = event?.sessionKey ?? ctx?.sessionKey;
      children.delete(norm(key));
      paint(key, "off", { fresh: true });
    },
    gateway_start() {
      // Names for panes that were open before this gateway (re)started.
      enqueue(() => resolve(true));
    },
  };

  return {
    handlers,
    refresh: () => enqueue(() => resolve(true)),
    idle: () => queue,
  };
}

export default {
  id: "tmux-status",
  name: "tmux status",
  description: "Drive the shared Claude/Codex tmux activity renderer from OpenClaw session events.",
  register(api) {
    const status = createTmuxStatus({ renderer: api.pluginConfig?.renderer || DEFAULT_RENDERER });
    for (const [hook, handler] of Object.entries(status.handlers)) {
      api.on(hook, (event, ctx) => {
        try {
          handler(event, ctx);
        } catch {
          // cosmetic; never surface into the turn
        }
      });
    }
    // A label set from the Control UI fires no typed hook. Pick it up.
    if (typeof api.registerHook === "function") {
      try {
        api.registerHook("session:patch", () => {
          setTimeout(status.refresh, 300);
        }, { name: "tmux-status-label" });
      } catch {
        // older hosts: names update on the next event instead
      }
    }
  },
};
