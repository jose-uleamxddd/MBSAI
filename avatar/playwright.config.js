// Playwright config for the norivy-kiosk Electron E2E smoke.
// The tests drive the REAL renderer via _electron.launch(); there is no web
// server and no chromium download — Electron is launched from node_modules.
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "e2e",
  // Files are named *.e2e.js (not the default *.spec.js).
  testMatch: "**/*.e2e.js",
  // Electron cold start + VRM load attempts + WS round-trips: give it room.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  // Electron talks to port 8773; the mock binds it. One window per app, so serial.
  workers: 1,
  fullyParallel: false,
  // Flaky-hide is worse than a real red locally; fail loud.
  retries: 0,
  reporter: [["list"]],
});
