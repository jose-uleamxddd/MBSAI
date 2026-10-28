// norivy-kiosk — Electron main process (CommonJS).
// Lanza el backend Python (python -m norivy_kiosk) si no hay uno ya sirviendo
// en 127.0.0.1:8773, abre una ventana en modo kiosco (pantalla completa, sin
// salida para el cliente) con el avatar VRM "Norivy", y expone la config
// (WS url + paths de assets) al renderer por IPC.
//
// Contrato de spawn (debe coincidir con el backend):
//   command = process.env.PYTHON || <.venv/bin/python del proyecto> || (win32 ? "python" : "python3")
//   args    = ["-m", "norivy_kiosk"]
//   cwd     = <repo root> = path.join(__dirname, "..")
//   env     = { ...process.env, PYTHONPATH: path.join(__dirname, "..", "src") }

const {
  app,
  BrowserWindow,
  Menu,
  globalShortcut,
  ipcMain,
  screen,
} = require("electron");
const path = require("path");
const net = require("net");
const http = require("http");
const { spawn } = require("child_process");
const { resolvePython } = require("./lib/pythonResolver.js");

const PROJECT_ROOT = path.join(__dirname, "..");
const SRC_DIR = path.join(PROJECT_ROOT, "src");

const HOST = process.env.NORIVY_KIOSK_HOST || "127.0.0.1";
// Default 8773: 8771 es stage-v2 y 8772 desktop; el kiosco no debe chocar con ellos.
const PORT = Number(process.env.NORIVY_KIOSK_PORT || "8773");

// Espera total antes de cargar la UI aunque el backend no levante (no bloquea
// para siempre: el renderer reintenta el WS con backoff por su cuenta).
const HEALTH_TIMEOUT_MS = 15000;
const HEALTH_POLL_INTERVAL_MS = 400;

// Respawn del backend si crashea DURANTE la atención al público (no en cierre deliberado).
// Crash-loop guard: máx N reintentos en una ventana, luego avisa y para.
const BACKEND_MAX_RESTARTS = 3;
const BACKEND_RESTART_WINDOW_MS = 60000;
const BACKEND_RESTART_DELAY_MS = 1000;

let mainWindow = null;
let backendProc = null;
let isQuitting = false;
let backendRestartTimes = [];

/**
 * Conecta por TCP al puerto para detectar si algo ya está sirviendo.
 * @param {string} host
 * @param {number} port
 * @param {number} timeoutMs
 * @returns {Promise<boolean>}
 */
function isPortOpen(host, port, timeoutMs = 400) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;
    const done = (open) => {
      if (settled) return;
      settled = true;
      try {
        socket.destroy();
      } catch (_) {
        // socket ya cerrado; nada que hacer.
      }
      resolve(open);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
    socket.connect(port, host);
  });
}

/**
 * GET /healthz con timeout corto. Devuelve true si responde 2xx.
 * @param {string} host
 * @param {number} port
 * @param {number} timeoutMs
 * @returns {Promise<boolean>}
 */
function isHealthy(host, port, timeoutMs = 400) {
  return new Promise((resolve) => {
    const req = http.get(
      { host, port, path: "/healthz", timeout: timeoutMs },
      (res) => {
        res.resume(); // drenar el body para liberar el socket.
        resolve(
          res.statusCode !== undefined &&
            res.statusCode >= 200 &&
            res.statusCode < 300,
        );
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

/**
 * GET /healthz y verifica que la respuesta sea de un backend norivy-kiosk
 * (tiene `model` y `realtime_active`, y NO el `profile` de stage1). Evita
 * conectarnos por error a otro server que casualmente ocupe el mismo puerto.
 * @param {string} host
 * @param {number} port
 * @param {number} timeoutMs
 * @returns {Promise<boolean>}
 */
function isKioskBackend(host, port, timeoutMs = 600) {
  return new Promise((resolve) => {
    const req = http.get(
      { host, port, path: "/healthz", timeout: timeoutMs },
      (res) => {
        let body = "";
        res.on("data", (chunk) => (body += chunk));
        res.on("end", () => {
          try {
            const j = JSON.parse(body);
            resolve(
              !!j &&
                typeof j.model === "string" &&
                typeof j.realtime_active === "boolean" &&
                j.profile === undefined,
            );
          } catch (_) {
            resolve(false);
          }
        });
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

/**
 * Polea /healthz hasta que responda o se agote el presupuesto de tiempo.
 * @param {number} budgetMs
 * @returns {Promise<boolean>}
 */
async function waitForBackend(budgetMs) {
  const deadline = Date.now() + budgetMs;
  while (Date.now() < deadline) {
    if (await isHealthy(HOST, PORT, HEALTH_POLL_INTERVAL_MS)) return true;
    await new Promise((r) => setTimeout(r, HEALTH_POLL_INTERVAL_MS));
  }
  return false;
}

/**
 * Lanza el backend Python segun el contrato de spawn. Pipea stdout/stderr a la
 * consola de Electron con prefijo [backend].
 * @returns {import("child_process").ChildProcess | null}
 */
function spawnBackend() {
  const isWin = process.platform === "win32";
  const command = resolvePython({
    projectRoot: PROJECT_ROOT,
    platform: process.platform,
  });
  const args = ["-m", "norivy_kiosk"];

  const sep = isWin ? ";" : ":";
  const pythonPath = process.env.PYTHONPATH
    ? `${SRC_DIR}${sep}${process.env.PYTHONPATH}`
    : SRC_DIR;

  try {
    const proc = spawn(command, args, {
      cwd: PROJECT_ROOT,
      env: { ...process.env, PYTHONPATH: pythonPath },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    proc.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
    proc.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
    proc.on("error", (err) =>
      console.error(`[main] backend spawn error (${command}):`, err.message),
    );
    proc.on("exit", handleBackendExit);
    console.log(`[main] backend lanzado: ${command} ${args.join(" ")}`);
    return proc;
  } catch (err) {
    console.error(
      `[main] no pude lanzar el backend (${command}):`,
      err.message,
    );
    return null;
  }
}

/** Mata el backend spawneado si existe y sigue vivo. */
function killBackend() {
  if (backendProc && !backendProc.killed) {
    try {
      backendProc.kill();
    } catch (err) {
      console.warn("[main] no pude matar el backend:", err.message);
    }
  }
  backendProc = null;
}

/**
 * Maneja la salida del backend. En cierre deliberado no hace nada; ante un crash
 * inesperado durante el evento, relanza con un guard de crash-loop.
 * @param {number|null} code
 * @param {string|null} signal
 */
function handleBackendExit(code, signal) {
  console.log(`[main] backend exited code=${code} signal=${signal}`);
  if (isQuitting) return; // cierre deliberado: no relanzar
  backendProc = null;

  const now = Date.now();
  backendRestartTimes = backendRestartTimes.filter(
    (t) => now - t < BACKEND_RESTART_WINDOW_MS,
  );
  if (backendRestartTimes.length >= BACKEND_MAX_RESTARTS) {
    console.error(
      `[main] backend crasheó ${BACKEND_MAX_RESTARTS}x en ` +
        `${BACKEND_RESTART_WINDOW_MS / 1000}s — NO relanzo más. Revisá los logs.`,
    );
    return;
  }
  backendRestartTimes.push(now);
  console.warn(
    `[main] backend caído — relanzando en ${BACKEND_RESTART_DELAY_MS}ms ` +
      `(intento ${backendRestartTimes.length}/${BACKEND_MAX_RESTARTS})`,
  );
  setTimeout(() => {
    if (!isQuitting) backendProc = spawnBackend();
  }, BACKEND_RESTART_DELAY_MS);
}

function createMainWindow() {
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.bounds;

  mainWindow = new BrowserWindow({
    width: width || 1280,
    height: height || 800,
    x: 0,
    y: 0,
    frame: false,
    fullscreen: true,
    kiosk: true,
    backgroundColor: "#050608",
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  // Kiosco público: el cliente no puede salir de la app ni abrir otras páginas.
  const wc = mainWindow.webContents;
  wc.setVisualZoomLevelLimits(1, 1); // sin pellizco-zoom táctil
  wc.setWindowOpenHandler(() => ({ action: "deny" }));
  wc.on("will-navigate", (ev) => ev.preventDefault());
  wc.on("did-finish-load", () => {
    // Pantalla táctil: ocultar el cursor (opt-in por env).
    if (process.env.NORIVY_KIOSK_HIDE_CURSOR === "1") {
      wc.insertCSS("* { cursor: none !important; }").catch(() => {});
    }
  });

  // Cache limpio antes del primer load: sin esto Electron puede servir versiones
  // viejas de renderer.js / index.html aunque el archivo en disco haya cambiado.
  const ses = mainWindow.webContents.session;
  const clearWork = Promise.all([
    ses.clearCache(),
    ses.clearStorageData({
      storages: ["cachestorage", "shadercache", "filesystem"],
    }),
  ]).catch((e) => console.warn("[main] clear cache fallo:", e && e.message));
  // Red de seguridad: si clearCache/clearStorageData se cuelgan, cargar igual la
  // UI a los 2s — la pantalla pública NO puede quedar en negro.
  const clearTimeout = new Promise((r) => setTimeout(r, 2000));
  Promise.race([clearWork, clearTimeout]).finally(() => {
    mainWindow.loadFile(path.join(__dirname, "index.html"));
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    mainWindow.focus();
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function registerShortcuts() {
  // ÚNICA salida del modo kiosco: Ctrl+Shift+Q (personal de la tienda). Sin Esc,
  // F12 ni recarga: el cliente no debe poder salir de la app ni abrir DevTools.
  globalShortcut.register("CommandOrControl+Shift+Q", () => app.quit());
}

/**
 * Lee /healthz y avisa por consola si el backend arrancó SIN conectar a OpenAI
 * (realtime_active=false) — señal de key/red mala. El renderer también lo pinta.
 * @param {string} host
 * @param {number} port
 */
function logRealtimeReadiness(host, port) {
  const req = http.get(
    { host, port, path: "/healthz", timeout: 1000 },
    (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => {
        try {
          const j = JSON.parse(body);
          if (j && j.realtime_active === false) {
            console.warn(
              "[main] ⚠ backend arriba pero OpenAI NO conectó " +
                "(realtime_active=false) — revisá OPENAI_API_KEY / red.",
            );
          }
        } catch (_) {
          // body no parseable; nada que hacer.
        }
      });
    },
  );
  req.on("error", () => {});
  req.on("timeout", () => req.destroy());
}

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null); // sin barra de menú: nada con qué salir
  // NORIVY_KIOSK_NO_SPAWN=1 desactiva el spawn del backend Python. Solo para el
  // E2E de Electron (un WS mock ocupa el puerto); en runtime real NO se define,
  // así que el default sigue siendo spawnear como siempre.
  const noSpawn = process.env.NORIVY_KIOSK_NO_SPAWN === "1";
  // Evita doble backend: solo saltamos el spawn si lo que sirve en el puerto es
  // REALMENTE un backend kiosco (no otro server que ocupe el puerto).
  if (noSpawn) {
    console.log("[main] NORIVY_KIOSK_NO_SPAWN=1 — no lanzo backend (modo E2E)");
  } else if (await isKioskBackend(HOST, PORT)) {
    console.log(
      `[main] backend kiosco ya activo en ${HOST}:${PORT} — no lanzo otro`,
    );
  } else {
    if (await isPortOpen(HOST, PORT)) {
      console.warn(
        `[main] ${HOST}:${PORT} ocupado por OTRO server (no es el kiosco). ` +
          `Liberá el puerto o define NORIVY_KIOSK_PORT distinto.`,
      );
    }
    backendProc = spawnBackend();
  }

  createMainWindow();
  registerShortcuts();

  // Best-effort: esperamos a que /healthz responda. La UI ya carga en paralelo;
  // el renderer reintenta el WS solo, así que no bloqueamos el arranque.
  waitForBackend(HEALTH_TIMEOUT_MS).then((ok) => {
    if (!ok) {
      console.warn(
        `[main] backend no respondio /healthz en ${HEALTH_TIMEOUT_MS}ms`,
      );
      return;
    }
    // Responde, pero ¿conectó a OpenAI? Avisar si arrancó "vacío" (key/red).
    logRealtimeReadiness(HOST, PORT);
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on("window-all-closed", () => {
  isQuitting = true;
  killBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  isQuitting = true;
  killBackend();
  globalShortcut.unregisterAll();
});

// Config que consume el renderer al bootstrap. Paths relativos a __dirname para
// que funcionen igual en darwin y win32.
ipcMain.handle("stage:get-config", () => ({
  wsUrl: `ws://${HOST}:${PORT}/ws/kiosk`,
  vrmPath: "./models/vtuber.vrm",
  animationsBase: "./animations",
}));
