"use strict";
/**
 * Headless startup test — no BrowserWindow, no display. Runs the real, exact non-UI startup
 * sequence a founder's machine runs (real mongodb-memory-server, real Python subprocess, real
 * `/api/health` check) via Electron's own runtime (`app.getPath`/`app.isPackaged` need it), and
 * asserts the backend actually becomes healthy end to end.
 *
 * Run with: xvfb-run -a node_modules/.bin/electron test-startup.js --no-sandbox
 * (xvfb-run/a display is only needed because Electron's sandbox init still touches a GPU context
 * even with no window; --no-sandbox is required when running as root, e.g. in a container.)
 */
const { app } = require("electron");
const http = require("http");

async function checkHealth(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/api/health", timeout: 2000 }, (res) => {
      res.resume();
      resolve(res.statusCode);
    });
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
  });
}

async function main() {
  await app.whenReady();
  const { startBackendStack, cleanup, getFreePort } = require("./main.js");

  // getFreePort() is exercised standalone too — the ordering bug this app already fixed once
  // (backend port / static-server port had to be resolved before either process started) means
  // it's worth a direct assertion, not just an implicit one via a successful startBackendStack.
  const port1 = await getFreePort();
  const port2 = await getFreePort();
  if (!(port1 > 0 && port2 > 0 && port1 !== port2)) {
    throw new Error(`getFreePort() did not return two distinct real ports (got ${port1}, ${port2})`);
  }
  console.log(`OK: getFreePort() returns distinct real ports (${port1}, ${port2})`);

  const statuses = [];
  const { staticPort, backendPort } = await startBackendStack((status, log) => {
    if (status) { statuses.push(status); console.log("STATUS:", status); }
    if (log) process.stdout.write(log);
  });

  if (!(staticPort > 0 && backendPort > 0)) {
    throw new Error(`startBackendStack() did not return real ports (static=${staticPort}, backend=${backendPort})`);
  }
  console.log(`OK: startBackendStack() returned real ports (static=${staticPort}, backend=${backendPort})`);

  const healthCode = await checkHealth(backendPort);
  if (healthCode !== 200) {
    throw new Error(`GET /api/health on the real spawned backend returned ${healthCode}, expected 200`);
  }
  console.log("OK: the real spawned backend answers /api/health with 200");

  const expectedStatuses = ["Starting local database", "Checking Python", "Installing backend",
    "Starting local web server", "Starting backend", "Waiting for backend"];
  const missing = expectedStatuses.filter((s) => !statuses.some((got) => got.startsWith(s)));
  if (missing.length) {
    throw new Error(`Expected onStatus to report these startup phases, never saw: ${missing.join(", ")}`);
  }
  console.log("OK: every expected startup phase was reported via onStatus");

  await cleanup();
  console.log("\nALL CHECKS PASSED");
  app.exit(0);
}

main().catch((err) => {
  console.error("BUG:", err.message || err);
  app.exit(1);
});
