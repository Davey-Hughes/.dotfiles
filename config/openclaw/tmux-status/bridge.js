// tmux-status bridge -- OpenClaw activity in the tmux tab, the way Claude Code
// and Codex already show it.
//
// The renderer is the shared ~/.config/.claude/hooks/tmux-status.sh. Claude Code
// and Codex call it from hooks that run inside the pane. OpenClaw's agent runs in
// the Gateway, which may be another machine, so nothing in the pane knows when a
// turn starts or ends. This bridge runs beside tmux instead: it connects to the
// Gateway as a read-only operator client, and on each session event asks
// tmux-session.sh which panes' TUI footers show that session, then runs the
// renderer once per pane with TMUX_PANE set:
//
//   sessions.changed: run starts, model call         working
//   session.tool start: ask_user / secrets request   blocked  (its result: working)
//   session.tool start: any other tool               working
//   sessions.changed: run settles                    idle, or failed on a real error;
//                                                    the last assistant text goes in
//                                                    so a turn ending on a question
//                                                    paints red
//   sessions.changed: child spawnedBy this session   subagent =<live children>
//   sessions.changed: new sessionId / reset          reset
//   sessions.changed: deleted / archived             off
//
// sessions.subscribe delivers sessions.changed and session.message for every
// session. Tool starts only arrive per session, so the bridge keeps a
// sessions.messages.subscribe open for each session some pane is showing.
//
// It also keeps each pane's @cc_name on the session label from the footer, which
// names the window, and it resets a pane whose TUI has switched to another
// session.
//
// Rendering is cosmetic, so it must never fall behind or crash. Events return
// immediately; the work goes on one serial queue, so a turn's events still land
// in order. Every child process has a timeout, and every failure is swallowed.
//
//   openclaw-tmux-status           the launcher; installs the pinned client and
//                                  runs this file (systemd: openclaw-tmux-status)

import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import { homedir, hostname } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SESSIONS = join(HERE, "tmux-session.sh");
const xdg = (name, fallback) => process.env[name] || join(homedir(), fallback);
const DEFAULT_RENDERER = join(xdg("XDG_CONFIG_HOME", ".config"), ".claude/hooks/tmux-status.sh");

const CHILD_TIMEOUT_MS = 5000;
const RESOLVE_TTL_MS = 1000; // a burst of events shares one footer read
const REPEAT_WORKING_MS = 2000; // `working` while already working does nothing
const START_RETRY_MS = 600; // the TUI redraws its footer after a reset
const POLL_MS = 5000; // new panes, switched sessions, renamed labels
const MAX_QUEUED = 64; // past this, drop new `working` paints

const DIALOG = (name, args) => name === "ask_user" || (name === "secrets" && args?.action === "request");
const DIALOG_RESULT = (name) => name === "ask_user" || name === "secrets";
const RUNNING = new Set(["running", "queued"]);
// A run that was stopped (Esc, /stop) is the user's own doing, not a failure.
const FAILED = new Set(["failed", "timeout"]);
const GONE = new Set(["deleted", "archive", "archived", "remove"]);

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

function textOf(message) {
  if (typeof message?.content === "string") return message.content;
  if (!Array.isArray(message?.content)) return "";
  return message.content
    .filter((c) => c?.type === "text" && typeof c.text === "string")
    .map((c) => c.text)
    .join("\n");
}

// Running, idle, or unknown (undefined) from one session row or delta.
function runningOf(row) {
  if (typeof row?.hasActiveRun === "boolean") return row.hasActiveRun;
  if (typeof row?.status === "string") return RUNNING.has(row.status);
  return undefined;
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
  const turns = new Map(); // session key -> { running, ended, blocked, sessionId }
  const lastText = new Map(); // session key -> last assistant text
  const children = new Map(); // parent session key -> Set of live child keys

  const norm = (key) => (typeof key === "string" ? key.trim().toLowerCase() : "");
  const turn = (key) => {
    let t = turns.get(key);
    if (!t) turns.set(key, (t = { running: false, ended: false, blocked: false, sessionId: null }));
    return t;
  };

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
        // to the old one; start from what is known about the new one.
        await render(pane, "off");
        await render(pane, "reset");
        const subs = children.get(key)?.size ?? 0;
        if (subs) await render(pane, "subagent", [`=${subs}`]);
        const t = turns.get(key);
        if (t?.running) await render(pane, t.blocked ? "blocked" : "working");
      }
      if (labelled.get(pane) !== label) {
        labelled.set(pane, label);
        await render(pane, "label", label ? [label] : []);
      }
    }
    return panes;
  }

  async function paintNow(key, verb, args, input, fresh) {
    if (!key) return 0;
    const panes = (await resolve(fresh)).filter((p) => p.key === key);
    for (const { pane } of panes) await render(pane, verb, args, input);
    return panes.length;
  }

  function paint(key, verb, { args = [], input = "", fresh = false, retry = false } = {}) {
    if (!key) return;
    enqueue(
      async () => {
        const hit = await paintNow(key, verb, args, input, fresh);
        if (!hit && retry) {
          setTimeout(() => enqueue(() => paintNow(key, verb, args, input, true)), startRetryMs);
        }
      },
      { droppable: verb === "working" },
    );
  }

  function working(key) {
    const t = turn(key);
    t.blocked = false;
    paint(key, "working");
  }

  function blocked(key) {
    turn(key).blocked = true;
    paint(key, "blocked");
  }

  function ended(key, row) {
    const t = turn(key);
    t.running = false;
    t.ended = true;
    t.blocked = false;
    if (FAILED.has(row?.status) && !row?.abortedLastRun) {
      paint(key, "failed");
      return;
    }
    const input = JSON.stringify({ hook_event_name: "Stop", last_assistant_message: lastText.get(key) ?? "" });
    paint(key, "idle", { input });
  }

  function setChild(child, parent, live) {
    const set = children.get(parent) ?? new Set();
    const before = set.size;
    if (live) set.add(child);
    else set.delete(child);
    if (set.size) children.set(parent, set);
    else children.delete(parent);
    if (set.size !== before) paint(parent, "subagent", { args: [`=${set.size}`] });
  }

  function forget(key) {
    turns.delete(key);
    lastText.delete(key);
    children.delete(key);
  }

  // One session row: a sessions.changed delta, or a sessions.subscribe snapshot.
  function sessionChanged(row) {
    const key = norm(row?.sessionKey ?? row?.key);
    if (!key) return;
    if (GONE.has(row.reason) || row.archived === true) {
      for (const [parent, set] of children) if (set.has(key)) setChild(key, parent, false);
      forget(key);
      paint(key, "off", { fresh: true });
      return;
    }
    const t = turn(key);
    const running = runningOf(row);
    const parent = norm(row.spawnedBy);
    if (parent && running !== undefined) setChild(key, parent, running);

    const replaced = typeof row.sessionId === "string" && t.sessionId && row.sessionId !== t.sessionId;
    if (typeof row.sessionId === "string") t.sessionId = row.sessionId;
    if (row.reason === "reset" || replaced) {
      // /new or /reset: same key, a fresh transcript.
      t.blocked = false;
      lastText.delete(key);
      paint(key, "reset", { fresh: true, retry: true });
    }

    if (running === true) {
      const starting = !t.running || row.phase === "start" || row.reason === "agent.run.started";
      // A new turn: the last turn's reply must not decide how this one ends.
      if (!t.running) lastText.delete(key);
      t.running = true;
      t.ended = false;
      if (starting || row.phase === "model") {
        if (t.blocked) paint(key, "blocked");
        else paint(key, "working");
      }
    } else if (running === false && (t.running || (row.reason === "agent.input.settled" && !t.ended))) {
      ended(key, row);
    }
  }

  function sessionMessage(payload) {
    const key = norm(payload?.sessionKey);
    const message = payload?.message;
    if (!key || !message) return;
    if (message.role === "user") lastText.delete(key);
    if (message.role === "assistant") {
      const text = textOf(message);
      if (text) lastText.set(key, text);
    }
    const blocks = Array.isArray(message.content) ? message.content : [];
    for (const block of blocks) {
      // A direct dialog call is recorded before it runs; a nested one only
      // after it returns, alongside its result, which session.tool covers.
      if (message.role === "assistant" && block?.type === "toolCall" && DIALOG(block.name, block.arguments)) {
        blocked(key);
      }
    }
    if (message.role === "toolResult" && DIALOG_RESULT(message.toolName) && turn(key).blocked) working(key);
  }

  function sessionTool(payload) {
    const key = norm(payload?.sessionKey);
    const data = payload?.data;
    if (!key || !data) return;
    if (data.phase === "start") {
      if (DIALOG(data.name, data.args)) blocked(key);
      else working(key);
    } else if (data.phase === "result" && DIALOG_RESULT(data.name)) {
      // The dialog was answered.
      working(key);
    }
  }

  function handle(event, payload) {
    try {
      if (event === "sessions.changed") sessionChanged(payload);
      else if (event === "session.message") sessionMessage(payload);
      else if (event === "session.tool") sessionTool(payload);
    } catch {
      // cosmetic; never take the bridge down
    }
  }

  // A fresh connection: what is running now, from a sessions.subscribe snapshot.
  // A session this side still thinks is mid-turn, that the snapshot says is
  // idle, ended while the bridge was disconnected.
  function seed(rows) {
    for (const row of Array.isArray(rows) ? rows : []) handle("sessions.changed", row);
  }

  return {
    handle,
    seed,
    refresh: (fresh = true) => enqueue(() => resolve(fresh)),
    idle: () => queue,
    shownKeys: () => new Set(resolved.panes.map((p) => p.key)),
  };
}

// --- connection ------------------------------------------------------------

export function readConfig(env = process.env) {
  const path =
    env.OPENCLAW_CONFIG_PATH ||
    [join(xdg("XDG_CONFIG_HOME", ".config"), "openclaw/openclaw.json"), join(homedir(), ".openclaw/openclaw.json")].find(
      (p) => fs.existsSync(p),
    );
  try {
    return path ? JSON.parse(fs.readFileSync(path, "utf8")) : {};
  } catch {
    return {};
  }
}

// Where the CLI on this machine would connect: the remote Gateway when
// gateway.mode is "remote", else the local one. OPENCLAW_TMUX_STATUS_URL,
// OPENCLAW_GATEWAY_TOKEN and OPENCLAW_GATEWAY_PASSWORD override.
export function gatewayTarget(config, env = process.env) {
  const gw = config?.gateway ?? {};
  const remote = gw.mode === "remote" ? (gw.remote ?? {}) : null;
  const str = (v) => (typeof v === "string" && v ? v : undefined);
  return {
    url: env.OPENCLAW_TMUX_STATUS_URL || str(remote?.url) || `ws://127.0.0.1:${gw.port ?? 18789}`,
    token: env.OPENCLAW_GATEWAY_TOKEN || str(remote ? remote.token : gw.auth?.token),
    password: env.OPENCLAW_GATEWAY_PASSWORD || str(remote ? remote.password : gw.auth?.password),
    tlsFingerprint: str(remote?.tlsFingerprint),
  };
}

function rawPublicKey(pem) {
  const der = crypto.createPublicKey(pem).export({ type: "spki", format: "der" });
  return der.subarray(der.length - 32);
}

// The bridge's own device identity: OpenClaw pairs devices, not processes, and
// this one is approved once (openclaw devices approve) like any other client.
function deviceStore(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const idPath = join(dir, "identity.json");
  const tokenPath = join(dir, "device-tokens.json");
  const readJson = (p, fallback) => {
    try {
      return JSON.parse(fs.readFileSync(p, "utf8"));
    } catch {
      return fallback;
    }
  };
  const writeJson = (p, value) => {
    const tmp = p + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(value), { mode: 0o600 });
    fs.renameSync(tmp, p);
  };
  let identity = readJson(idPath, null);
  if (!identity?.privateKeyPem) {
    const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
    const publicKeyPem = publicKey.export({ type: "spki", format: "pem" });
    const privateKeyPem = privateKey.export({ type: "pkcs8", format: "pem" });
    const deviceId = crypto.createHash("sha256").update(rawPublicKey(publicKeyPem)).digest("hex");
    identity = { deviceId, publicKeyPem, privateKeyPem };
    writeJson(idPath, identity);
  }
  const slot = ({ deviceId, role }) => `${deviceId}:${role}`;
  return {
    identity,
    hostDeps: {
      signDevicePayload: (pem, payload) =>
        crypto.sign(null, Buffer.from(payload, "utf8"), crypto.createPrivateKey(pem)).toString("base64url"),
      publicKeyRawBase64UrlFromPem: (pem) => rawPublicKey(pem).toString("base64url"),
      loadDeviceAuthToken: (p) => readJson(tokenPath, {})[slot(p)] ?? null,
      storeDeviceAuthToken: (p) => {
        const all = readJson(tokenPath, {});
        const current = all[slot(p)]?.token ?? null;
        if (p.expectedToken !== undefined && p.expectedToken !== current) return;
        all[slot(p)] = { token: p.token, scopes: p.scopes };
        writeJson(tokenPath, all);
      },
      clearDeviceAuthToken: (p) => {
        const all = readJson(tokenPath, {});
        if (p.expectedToken !== undefined && all[slot(p)]?.token !== p.expectedToken) return;
        delete all[slot(p)];
        writeJson(tokenPath, all);
      },
      logError: (m) => console.error(m),
    },
  };
}

function describeError(error) {
  const details = error?.details ?? error?.gatewayError?.details;
  const parts = [String(error?.message ?? error)];
  const requestId = details?.requestId ?? details?.pairing?.requestId;
  if (requestId) parts.push(`approve this device on the Gateway host: openclaw devices approve ${requestId}`);
  return parts.join(" -- ");
}

async function main() {
  const modules = process.env.OPENCLAW_TMUX_STATUS_MODULES;
  if (!modules) {
    console.error("OPENCLAW_TMUX_STATUS_MODULES is unset; start the bridge with openclaw-tmux-status");
    process.exit(64);
  }
  const require = createRequire(join(modules, "noop.js"));
  const { GatewayClient } = await import(pathToFileURL(require.resolve("@openclaw/gateway-client")).href);

  const target = gatewayTarget(readConfig());
  const store = deviceStore(join(xdg("XDG_STATE_HOME", ".local/state"), "openclaw-tmux-status"));
  const status = createTmuxStatus();

  let ready = false;
  let subscribed = new Set();
  let syncing = Promise.resolve();
  let client;

  // Keep one message subscription per session some pane is showing.
  const sync = () =>
    (syncing = syncing.then(async () => {
      if (!ready) return;
      const wanted = status.shownKeys();
      for (const key of wanted) {
        if (subscribed.has(key)) continue;
        try {
          await client.request("sessions.messages.subscribe", { key });
          subscribed.add(key);
        } catch (e) {
          console.error(`subscribe ${key}: ${describeError(e)}`);
        }
      }
      for (const key of subscribed) {
        if (wanted.has(key)) continue;
        subscribed.delete(key);
        client.request("sessions.messages.unsubscribe", { key }).catch(() => {});
      }
    }));

  client = new GatewayClient({
    url: target.url,
    token: target.token,
    password: target.password,
    tlsFingerprint: target.tlsFingerprint,
    clientName: "gateway-client",
    clientDisplayName: `tmux-status (${hostname()})`,
    mode: "ui",
    role: "operator",
    scopes: ["operator.read"],
    caps: ["tool-events"],
    platform: process.platform,
    deviceIdentity: store.identity,
    hostDeps: store.hostDeps,
    onHelloOk: async () => {
      ready = true;
      subscribed = new Set();
      console.error(`connected to ${target.url}`);
      try {
        const sub = await client.request("sessions.subscribe", { limit: 100 });
        status.seed(sub?.list?.sessions);
      } catch (e) {
        console.error(`sessions.subscribe: ${describeError(e)}`);
      }
      await status.refresh(true);
      sync();
    },
    onEvent: (ev) => status.handle(ev.event, ev.payload),
    onClose: (code, reason) => {
      ready = false;
      console.error(`disconnected (${code}${reason ? `: ${reason}` : ""})`);
    },
    onConnectError: (e) => console.error(`connect: ${describeError(e)}`),
    // Pairing pending or credentials refused: exit, and let systemd retry later.
    onReconnectPaused: (info) => {
      console.error(`reconnect paused: ${info.reason || info.detailCode || info.code}`);
      process.exit(75);
    },
  });

  setInterval(async () => {
    await status.refresh(true);
    sync();
  }, POLL_MS);
  for (const signal of ["SIGTERM", "SIGINT"]) {
    process.on(signal, () => {
      client.stop();
      process.exit(0);
    });
  }
  client.start();
}

// Only `bridge.js run` connects; importing it (the tests) does not.
if (process.argv[2] === "run" && pathToFileURL(fs.realpathSync(process.argv[1])).href === import.meta.url) {
  main().catch((e) => {
    console.error(e?.stack ?? e);
    process.exit(1);
  });
}
