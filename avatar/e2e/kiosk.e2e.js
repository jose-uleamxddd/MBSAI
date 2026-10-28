// End-to-end smoke del shell Electron de norivy-kiosk.
//
// Levanta un WS mock (mock-backend.js) en 127.0.0.1:8773 y lanza la app REAL en
// modo kiosco. Verifica lo que es del shell (main.js): ventana kiosco, config por
// IPC, bloqueo de navegación y que el renderer conecta al WS. NO aserta el DOM del
// renderer (cambia con la UI); no depende de VRM/WebGL.

const { test, expect, _electron: electron } = require("@playwright/test");
const path = require("path");
const { startMockBackend } = require("./mock-backend");

const APP_DIR = path.join(__dirname, "..");

/**
 * Lanza Electron con el spawn del backend desactivado (el mock ocupa el puerto).
 * @param {import("@playwright/test").ElectronApplication[]} sink para limpiar al final.
 * @returns {Promise<{ app: import("@playwright/test").ElectronApplication, page: import("@playwright/test").Page }>}
 */
async function launchApp(sink) {
  const app = await electron.launch({
    args: ["."],
    cwd: APP_DIR,
    env: { ...process.env, NORIVY_KIOSK_NO_SPAWN: "1" },
    timeout: 30_000,
  });
  sink.push(app);
  return { app, page: await app.firstWindow({ timeout: 20_000 }) };
}

test.describe("norivy-kiosk shell", () => {
  /** @type {import("@playwright/test").ElectronApplication[]} */
  let apps = [];
  /** @type {Array<{ close: () => Promise<void> }>} */
  let servers = [];

  test.afterEach(async () => {
    await Promise.all(apps.map((a) => a.close().catch(() => {})));
    await Promise.all(servers.map((s) => s.close().catch(() => {})));
    apps = [];
    servers = [];
  });

  test("arranca en modo kiosco y el renderer conecta al WS /ws/kiosk", async () => {
    const mock = await startMockBackend({ realtimeActive: true, micMuted: true });
    servers.push(mock);

    const { app, page } = await launchApp(apps);

    // Ventana en modo kiosco + pantalla completa.
    const flags = await app.evaluate(({ BrowserWindow }) => {
      const w = BrowserWindow.getAllWindows()[0];
      return { kiosk: w.isKiosk(), fullScreen: w.isFullScreen() };
    });
    expect(flags).toEqual({ kiosk: true, fullScreen: true });

    // Config por IPC: apunta al WS del kiosco.
    const cfg = await page.evaluate(() => window.stage.getConfig());
    expect(cfg.wsUrl).toBe("ws://127.0.0.1:8773/ws/kiosk");

    // El renderer abrió el WS (recibió el hello del mock).
    await expect.poll(() => mock.connections, { timeout: 15_000 }).toBeGreaterThan(0);
  });

  test("el cliente no puede navegar fuera de la app", async () => {
    const mock = await startMockBackend();
    servers.push(mock);

    const { page } = await launchApp(apps);
    const before = page.url();

    await page.evaluate(() => {
      window.location.href = "https://example.com";
    });
    await page.waitForTimeout(500);

    expect(page.url()).toBe(before);
  });
});
