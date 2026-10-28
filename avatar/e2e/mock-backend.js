// WS mock backend for the Electron E2E — stands in for the Python norivy-kiosk
// backend so the renderer (three/DOM/WebSocket/AudioContext) can be driven
// through its happy path WITHOUT OpenAI or the real backend.
//
// It speaks the WS PROTOCOL that renderer.js consumes:
//   server->client: hello · status · speech_started · audio · transcript ·
//                   speech_done · mic_state · session_reset
//   client->server: start (cliente toca la pantalla) · reset (fin de atención)
//
// It also serves GET /healthz with a kiosk-shaped body ({ model, realtime_active,
// ... } and NO `profile`) so main.js's isKioskBackend() skips spawning the real
// backend even if NORIVY_KIOSK_NO_SPAWN is not set — belt and suspenders.

const http = require("http");
const { WebSocketServer } = require("ws");

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 8773;
const WS_PATH = "/ws/kiosk";

// A tiny valid base64 PCM16 chunk (a few silent samples). The renderer decodes
// it via base64ToInt16 → schedules it on the AudioContext; content is
// irrelevant, we only need enqueueAudio to run without throwing.
const TINY_PCM16_BASE64 = Buffer.alloc(16).toString("base64");

/**
 * Start the mock backend.
 * @param {object} [opts]
 * @param {string} [opts.host]
 * @param {number} [opts.port]
 * @param {string} [opts.characterName]
 * @param {string} [opts.storeName]
 * @param {boolean} [opts.realtimeActive] emit hello.realtime_active (false = fatal banner test).
 * @param {boolean} [opts.micMuted] initial mic state announced in hello.
 * @returns {Promise<{ url: string, port: number, close: () => Promise<void>, received: object[] }>}
 */
function startMockBackend(opts = {}) {
  const host = opts.host || DEFAULT_HOST;
  const port = opts.port || DEFAULT_PORT;
  const characterName = opts.characterName || "Norivy";
  const storeName = opts.storeName || "Tienda E2E";
  const realtimeActive = opts.realtimeActive !== false; // default true
  const initialMicMuted = opts.micMuted !== false; // default true (matches real backend)

  // Every client->server message the renderer sent, for assertions in the test.
  const received = [];
  // Cuántos WS abrió el renderer (prueba de que arrancó y conectó).
  let connections = 0;

  const httpServer = http.createServer((req, res) => {
    if (req.method === "GET" && req.url === "/healthz") {
      res.writeHead(200, { "content-type": "application/json" });
      // Forma kiosco: `model` + `realtime_active`, sin `profile` → isKioskBackend() === true.
      res.end(
        JSON.stringify({
          model: "gpt-realtime-mock",
          voice: "coral",
          realtime_active: realtimeActive,
          openai_connected: realtimeActive,
          mic_alive: !initialMicMuted,
        }),
      );
      return;
    }
    res.writeHead(404);
    res.end();
  });

  const wss = new WebSocketServer({ server: httpServer, path: WS_PATH });

  wss.on("connection", (ws) => {
    connections += 1;

    send(ws, {
      type: "hello",
      character_name: characterName,
      store_name: storeName,
      model: "gpt-realtime-mock",
      voice: "coral",
      realtime_active: realtimeActive,
      mic_muted: initialMicMuted,
    });
    send(ws, { type: "status", state: "connected" });

    ws.on("message", (data) => {
      let msg;
      try {
        msg = JSON.parse(data.toString());
      } catch {
        return; // ignore malformed client frames, like the real hub would
      }
      received.push(msg);

      if (msg.type === "start") {
        // Saludo al tocar la pantalla: speech starts, one audio chunk, one transcript word,
        // then done — the minimal sequence the renderer needs for a caption.
        send(ws, { type: "speech_started" });
        send(ws, { type: "audio", pcm16: TINY_PCM16_BASE64 });
        send(ws, { type: "transcript", delta: "Hola" });
        send(ws, { type: "speech_done" });
      } else if (msg.type === "reset") {
        send(ws, { type: "session_reset" });
        send(ws, { type: "mic_state", muted: true });
      }
    });
  });

  return new Promise((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(port, host, () => {
      resolve({
        url: `ws://${host}:${port}${WS_PATH}`,
        port,
        received,
        get connections() {
          return connections;
        },
        close: () =>
          new Promise((res) => {
            wss.clients.forEach((c) => c.terminate());
            wss.close(() => httpServer.close(() => res()));
          }),
      });
    });
  });
}

/**
 * JSON-encode and send a frame if the socket is still open.
 * @param {import("ws").WebSocket} ws
 * @param {object} payload
 */
function send(ws, payload) {
  if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(payload));
}

module.exports = { startMockBackend };
