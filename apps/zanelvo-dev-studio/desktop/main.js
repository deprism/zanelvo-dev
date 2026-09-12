"use strict";
/**
 * Zanelvo Dev Studio desktop shell.
 *
 * Wraps the existing FastAPI backend + React frontend in an Electron window — no rewrite of
 * either. On launch this process:
 *   1. starts a real, persistent local MongoDB (mongodb-memory-server: a genuine mongod binary,
 *      not a mock — data lives under the OS user-data dir and survives restarts),
 *   2. spawns the FastAPI backend (`python -m uvicorn server:app`) against it, generating and
 *      persisting a JWT secret + admin password on first run,
 *   3. serves the already-built frontend (apps/zanelvo-dev-studio/frontend/dist) from a small
 *      local static server, injecting the backend's chosen port so the page can reach it,
 *   4. opens a window once the backend's /api/health check passes.
 *
 * Requires Python 3.11+ on PATH (see README.md) — this shell does not bundle a Python runtime.
 * If it's missing, or the backend's own dependencies aren't installed, the window shows a plain
 * error/instructions screen instead of a silent failure.
 *
 * First-launch dependency install runs as a real streamed subprocess (never spawnSync) with live
 * progress shown in the window and a bounded timeout — a blocking spawnSync here previously froze
 * the whole Electron process (the OS marks the window "Not Responding") for the entire pip
 * install, with no page loaded yet to show what was even happening, which is exactly what made a
 * merely-slow first install look hung enough to force-quit. See ensureBackendDeps/createWindow.
 */
const { app, BrowserWindow, shell } = require("electron");
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

const STATE = { backend: null, mongo: null, pipInstall: null, backendPort: null, staticPort: null, window: null };

const LOADING_HTML = `<!doctype html><body style="font:14px system-ui;margin:0;height:100vh;
display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;
background:#0B0A16;color:#fff">
<div style="width:28px;height:28px;border:3px solid rgba(255,255,255,.15);
border-top-color:#8b5cf6;border-radius:50%;animation:spin 0.8s linear infinite"></div>
<div id="status" style="color:rgba(255,255,255,.75)">Starting Zanelvo Dev Studio…</div>
<pre id="log" style="max-width:640px;max-height:220px;overflow:auto;color:rgba(255,255,255,.4);
font-size:11px;white-space:pre-wrap;margin:0;padding:0 16px"></pre>
<style>@keyframes spin{to{transform:rotate(360deg)}}</style>
<script>
  const statusEl = document.getElementById("status");
  const logEl = document.getElementById("log");
  window.electronAPI?.onStatus((status, log) => {
    if (status) statusEl.textContent = status;
    if (log) {
      logEl.textContent += log;
      logEl.scrollTop = logEl.scrollHeight;
    }
  });
</script>
</body>`;

function resourceDir(name) {
  // Dev: `electron .` run from this folder — sibling ../frontend/dist and ../backend.
  // Packaged: electron-builder copied them into resourcesPath via `extraResources` (package.json).
  return app.isPackaged
    ? path.join(process.resourcesPath, name)
    : path.join(__dirname, "..", name === "backend" ? "backend" : "frontend/dist");
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function loadOrCreateSecrets() {
  const file = path.join(app.getPath("userData"), "secrets.json");
  if (fs.existsSync(file)) {
    try {
      return JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      // fall through to regenerate — a corrupt file must not brick the app
    }
  }
  const secrets = {
    jwtSecret: crypto.randomBytes(32).toString("hex"),
    adminPassword: crypto.randomBytes(12).toString("base64url"),
  };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(secrets, null, 2), { mode: 0o600 });
  return secrets;
}

function findPython() {
  for (const cmd of ["python3", "python"]) {
    const r = spawnSync(cmd, ["--version"]);
    if (r.status === 0) return cmd;
  }
  return null;
}

const PIP_INSTALL_TIMEOUT_MS = 20 * 60 * 1000; // 20 minutes — generous for a slow/cold pip cache,
// but bounded: a hang here must surface as a clear error, never sit silently forever.

/** Best-effort dependency install — makes first launch work without a manual `pip install` step
 * when pip has network access; a no-op (fast) on every later launch once packages are present.
 * Runs as a real streamed child process (never spawnSync, which would block this entire process —
 * including window repaint/input — for however long pip takes) so the window stays responsive and
 * `onLog` can show real progress instead of a frozen screen. */
function ensureBackendDeps(pythonCmd, backendDir, onLog) {
  const reqs = ["requirements.txt", "requirements-devstudio.txt"].filter((f) =>
    fs.existsSync(path.join(backendDir, f)),
  );
  if (!reqs.length) return Promise.resolve({ ok: true });
  const args = ["-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary"];
  for (const f of reqs) args.push("-r", f);
  return new Promise((resolve) => {
    const child = spawn(pythonCmd, args, { cwd: backendDir });
    STATE.pipInstall = child;
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      killTree(child);
    }, PIP_INSTALL_TIMEOUT_MS);
    child.stdout?.on("data", (d) => onLog?.(d.toString(), ""));
    child.stderr?.on("data", (d) => { stderr += d.toString(); onLog?.("", d.toString()); });
    child.on("error", (err) => {
      clearTimeout(timer);
      STATE.pipInstall = null;
      resolve({ ok: false, stderr: `Could not start ${pythonCmd}: ${err.message}` });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      STATE.pipInstall = null;
      if (timedOut) {
        resolve({ ok: false, stderr: `Timed out after ${PIP_INSTALL_TIMEOUT_MS / 60000} minutes.\n${stderr}` });
        return;
      }
      resolve({ ok: code === 0, stderr });
    });
  });
}

async function waitForHealth(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ok = await new Promise((resolve) => {
      const req = http.get({ host: "127.0.0.1", port, path: "/api/health", timeout: 1500 }, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
      req.on("error", () => resolve(false));
      req.on("timeout", () => { req.destroy(); resolve(false); });
    });
    if (ok) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
  ".woff2": "font/woff2", ".ico": "image/x-icon" };

/** Tiny static file server for the pre-built frontend — same-origin for the app, with the
 * backend's actual port injected into index.html so the page can reach it on a different port. */
function startStaticServer(frontendDir, backendPort) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      let reqPath = decodeURIComponent(req.url.split("?")[0]);
      if (reqPath === "/") reqPath = "/index.html";
      let filePath = path.join(frontendDir, reqPath);
      if (!filePath.startsWith(frontendDir)) { res.writeHead(403); res.end(); return; } // no traversal
      if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        filePath = path.join(frontendDir, "index.html"); // SPA fallback
      }
      const ext = path.extname(filePath);
      if (filePath.endsWith("index.html")) {
        let html = fs.readFileSync(filePath, "utf8");
        const inject = `<script>window.__DESKTOP_API_BASE__=${JSON.stringify(`http://127.0.0.1:${backendPort}/api`)};</script>`;
        html = html.includes("</head>") ? html.replace("</head>", `${inject}</head>`) : inject + html;
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(html);
        return;
      }
      fs.readFile(filePath, (err, data) => {
        if (err) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
        res.end(data);
      });
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

function killTree(child) {
  if (!child || child.killed) return;
  try {
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    else child.kill("SIGTERM");
  } catch { /* best effort — the OS reclaims an orphaned child on app exit regardless */ }
}

/** The whole non-UI startup sequence, isolated from BrowserWindow so it can be exercised
 * headlessly (no window ever created) under `electron test-startup.js` (Electron still needs its
 * own runtime for `app.getPath`/`app.isPackaged`, but never opens a display) — see
 * desktop/test-startup.js, and the require.main guard at the bottom of this file that keeps
 * requiring this module for that purpose from also auto-starting the real app. */
async function startBackendStack(onStatus = () => {}) {
  onStatus("Starting local database…");
  const { MongoMemoryServer } = require("mongodb-memory-server");
  const dbPath = path.join(app.getPath("userData"), "mongodb-data");
  fs.mkdirSync(dbPath, { recursive: true });
  const mongo = await MongoMemoryServer.create({ instance: { dbPath, storageEngine: "wiredTiger" } });
  STATE.mongo = mongo;

  onStatus("Checking Python…");
  const pythonCmd = findPython();
  if (!pythonCmd) {
    throw new Error(
      "Python 3.11+ was not found on PATH. Zanelvo Dev Studio's backend runs on Python — " +
      "install it from python.org (check \"Add to PATH\" during install) and relaunch."
    );
  }

  const backendDir = resourceDir("backend");
  onStatus("Installing backend dependencies (first launch only)…");
  const deps = await ensureBackendDeps(pythonCmd, backendDir, (out, err) => onStatus(undefined, out || err));
  if (!deps.ok) {
    throw new Error(
      "Could not install the backend's Python dependencies automatically:\n\n" +
      `${(deps.stderr || "").slice(-800)}\n\n` +
      `Try running manually: ${pythonCmd} -m pip install -r requirements.txt -r requirements-devstudio.txt\n` +
      `(from ${backendDir})`
    );
  }

  // Backend port and static-server port are both determined before either process starts: the
  // backend's CORS_ORIGINS needs the static server's real (OS-assigned) port, and the static
  // server's index.html injection needs the backend's port — starting them in the wrong order
  // (or re-probing a "placeholder" port that a second listen() call might not actually get) was
  // an earlier bug here; this ordering removes that race entirely.
  const backendPort = await getFreePort();
  onStatus("Starting local web server…");
  const frontendDir = resourceDir("frontend");
  const staticServer = await startStaticServer(frontendDir, backendPort);
  const staticPort = staticServer.address().port;
  STATE.staticPort = staticPort;
  STATE.backendPort = backendPort;

  onStatus("Starting backend…");
  const secrets = loadOrCreateSecrets();
  const env = {
    ...process.env,
    MONGO_URL: mongo.getUri(),
    DB_NAME: "zanelvo_devstudio_desktop",
    JWT_SECRET: secrets.jwtSecret,
    ADMIN_PASSWORD: secrets.adminPassword,
    CORS_ORIGINS: `http://127.0.0.1:${staticPort}`,
  };
  const backend = spawn(pythonCmd, ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", String(backendPort)],
    { cwd: backendDir, env });
  STATE.backend = backend;
  backend.stdout?.on("data", (d) => onStatus(undefined, d.toString()));
  backend.stderr?.on("data", (d) => onStatus(undefined, d.toString()));

  onStatus("Waiting for backend to become ready…");
  const healthy = await waitForHealth(backendPort);
  if (!healthy) {
    throw new Error(
      "The backend did not become ready in time. Check the logs in the app's log window for the " +
      "actual Python error (missing dependency, port conflict, etc.)."
    );
  }

  return { staticPort, backendPort };
}

async function cleanup() {
  killTree(STATE.pipInstall);
  killTree(STATE.backend);
  if (STATE.mongo) await STATE.mongo.stop().catch(() => {});
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    title: "Zanelvo Dev Studio",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  STATE.window = win;
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });

  // Shown immediately, before any backend work starts — the window must never sit blank while
  // startBackendStack runs (see the file header comment for why that mattered).
  await win.loadURL("data:text/html," + encodeURIComponent(LOADING_HTML));

  try {
    const { staticPort } = await startBackendStack((status, log) => {
      if (status || log) win.webContents.send?.("startup-status", status, log);
      if (log) console.log(log);
    });
    await win.loadURL(`http://127.0.0.1:${staticPort}/`);
  } catch (err) {
    const escaped = String(err.message || err).replace(/&/g, "&amp;").replace(/</g, "&lt;");
    await win.loadURL(
      "data:text/html," + encodeURIComponent(
        `<body style="font:14px system-ui;padding:32px;white-space:pre-wrap;background:#0B0A16;color:#fff">` +
        `<h2>Zanelvo Dev Studio couldn't start</h2><pre>${escaped}</pre></body>`
      )
    );
  }
}

// Guarded so `require("./main.js")` (see test-startup.js) can reuse startBackendStack/cleanup
// without ALSO triggering the real app lifecycle (a second, redundant createWindow()) — only
// `electron main.js` itself (require.main === module) auto-starts the window.
if (require.main === module) {
  app.whenReady().then(createWindow);
  app.on("window-all-closed", async () => { await cleanup(); if (process.platform !== "darwin") app.quit(); });
  app.on("before-quit", cleanup);
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });

  // Belt-and-suspenders against an orphaned backend/mongod: `before-quit`/`window-all-closed`
  // cover a normal quit, but killTree() is synchronous (safe to call from a signal handler) so it
  // also runs here for the case those Electron events don't fire — e.g. the OS sending SIGTERM/
  // SIGINT directly to this process (killed externally, not via a window close) rather than
  // app.quit().
  process.on("SIGINT", () => { killTree(STATE.backend); process.exit(0); });
  process.on("SIGTERM", () => { killTree(STATE.backend); process.exit(0); });
}

module.exports = { startBackendStack, cleanup, resourceDir, getFreePort };
