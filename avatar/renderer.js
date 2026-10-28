// renderer.js — pantalla del kiosco de Norivy (tienda de motos y bicicletas).
//
// Carga el avatar VRM (three + @pixiv/three-vrm), reproduce el audio de OpenAI
// Realtime (PCM16 24 kHz por el WS local), anima el labio por amplitud y pinta la
// atención al cliente: subtítulos, tarjetas de productos, aplicabilidad, carrito
// y la cotización con código QR. La lógica de qué mostrar vive en lib/kioskView.js
// (pura y testeada); acá solo hay DOM, audio y three.
//
// Los specifiers "bare" ("three", "@pixiv/three-vrm") los resuelve el importmap
// de index.html.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import {
  VRMAnimationLoaderPlugin,
  createVRMAnimationClip,
} from "@pixiv/three-vrm-animation";
import { EmoteEngine } from "./emotes.js";
import { base64ToInt16 } from "./lib/pcm.js";
import { computeStartTime } from "./lib/audioSchedule.js";
import { canApplyStatus } from "./lib/statusMachine.js";
import { rmsToMouthAmp } from "./lib/lipsync.js";
import { advanceReveal } from "./lib/captionPacer.js";
import { GREETING_CLIP } from "./lib/emoteLexicon.js";
import {
  applyFrame,
  canStartSession,
  formatPrice,
  idleStep,
  initialView,
  startSession,
  stockLabel,
  yearRange,
  safeHttpUrl,
} from "./lib/kioskView.js";
import { renderQr } from "./lib/qr.js";

// ---------------------------------------------------------------------------
// Constantes
// ---------------------------------------------------------------------------
const DEFAULT_CONFIG = Object.freeze({
  wsUrl: "ws://127.0.0.1:8773/ws/kiosk",
  vrmPath: "./models/vtuber.vrm",
  animationsBase: "./animations",
});
const AUDIO_SAMPLE_RATE = 24000;
const IDLE_CLIP_REL = "idle/idle_breath.vrma";
const CROSSFADE_SECONDS = 0.3;
const VRM_LOAD_MAX_ATTEMPTS = 3;
const VRM_LOAD_RETRY_BASE_MS = 400;
const MAX_BUBBLES = 2;
// Velocidad de "escritura" de los subtítulos de Norivy (chars/seg): el transcript
// llega casi instantáneo y se revela a ritmo de habla para acompañar la voz.
const CAPTION_CHARS_PER_SEC = 16;
const WS_RECONNECT_MIN_MS = 400;
const WS_RECONNECT_MAX_MS = 5000;
const LIPSYNC_GAIN = 5.0;
const LIPSYNC_SMOOTHING = 0.5;
const MOUTH_DECAY = 0.85;
// Inactividad: aviso «¿sigue ahí?» y cierre. Con la cotización en pantalla se da
// más margen (el cliente está escaneando el QR con el celular).
const IDLE_WARN_MS = 45_000;
const IDLE_RESET_MS = 60_000;
const IDLE_QUOTE_EXTRA_MS = 60_000;
const IDLE_TICK_MS = 1000;

// ---------------------------------------------------------------------------
// Estado
// ---------------------------------------------------------------------------
const state = {
  config: { ...DEFAULT_CONFIG },
  characterName: "Norivy",
  storeName: "",

  scene: null,
  camera: null,
  renderer: null,
  clock: null,
  canvas: null,

  avatar: null,
  vrm: null,
  mixer: null,
  idleAction: null,
  emoteAction: null,
  emoteEngine: null,

  ws: null,
  reconnectDelay: WS_RECONNECT_MIN_MS,
  reconnectTimer: null,

  view: initialView(),
  lastActivity: Date.now(),
  norivySpeaking: false,

  audioCtx: null,
  analyser: null,
  analyserData: null,
  nextStartTime: 0,
  scheduledSources: new Set(),
  mouthAmp: 0,

  bubbles: {
    assistant: { el: null, text: "" },
    user: { el: null, text: "" },
  },
};

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
async function bootstrap() {
  await loadConfig();
  initThree();
  const avatar = await buildAvatar(state.config.vrmPath);
  if (avatar) {
    adoptAvatar(avatar);
    ensureAvatarVisible();
  } else {
    setStatus("asset_error", "sin avatar — la voz y la pantalla siguen");
  }
  state.emoteEngine = new EmoteEngine((relPath) => playEmote(animPath(relPath)), {
    // Un gesto activo no se corta con otro hasta volver a idle.
    isBusy: () => state.emoteAction !== null,
  });
  wireUi();
  render();
  connectWs(state.config.wsUrl);
  startRenderLoop();
  setInterval(checkIdle, IDLE_TICK_MS);
}

async function loadConfig() {
  try {
    if (window.stage && typeof window.stage.getConfig === "function") {
      const cfg = await window.stage.getConfig();
      state.config = {
        wsUrl: cfg.wsUrl || DEFAULT_CONFIG.wsUrl,
        vrmPath: cfg.vrmPath || DEFAULT_CONFIG.vrmPath,
        animationsBase: cfg.animationsBase || DEFAULT_CONFIG.animationsBase,
      };
    }
  } catch (err) {
    console.warn("[renderer] getConfig falló, uso defaults:", err);
  }
}

function animPath(relPath) {
  return `${state.config.animationsBase}/${relPath}`;
}

// ---------------------------------------------------------------------------
// Three.js — el canvas ocupa SOLO la columna del avatar (CSS grid)
// ---------------------------------------------------------------------------
function canvasSize() {
  const c = state.canvas;
  return { w: Math.max(c.clientWidth, 1), h: Math.max(c.clientHeight, 1) };
}

function initThree() {
  const canvas = document.getElementById("avatar-canvas");
  state.canvas = canvas;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    premultipliedAlpha: true,
    powerPreference: "high-performance",
    stencil: false,
  });
  const { w, h } = canvasSize();
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w, h, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  scene.background = null;

  // Encuadre de busto: el cliente la mira de frente en la pantalla.
  const camera = new THREE.PerspectiveCamera(24, w / h, 0.1, 50);
  camera.position.set(0, 1.4, 3.4);
  camera.lookAt(new THREE.Vector3(0, 1.3, 0));

  const key = new THREE.DirectionalLight(0xffffff, 1.4);
  key.position.set(2, 4, 3);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x9fb6ff, 0.55);
  fill.position.set(-3, 2, 1);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 0.6);
  rim.position.set(0, 3, -3);
  scene.add(rim);
  scene.add(new THREE.AmbientLight(0xffffff, 0.18));

  state.scene = scene;
  state.camera = camera;
  state.renderer = renderer;
  state.clock = new THREE.Clock();

  window.addEventListener("resize", onResize);
}

function onResize() {
  if (!state.renderer || !state.camera) return;
  const { w, h } = canvasSize();
  state.renderer.setSize(w, h, false);
  state.camera.aspect = w / h;
  state.camera.updateProjectionMatrix();
}

// ---------------------------------------------------------------------------
// Carga del VRM
// ---------------------------------------------------------------------------
async function loadVrmOnce(vrmPath) {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
  const gltf = await loader.loadAsync(vrmPath);
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error(`el archivo no tiene VRM userData: ${vrmPath}`);
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  // Los VRM 0.x miran a +Z: se rotan para que miren a la cámara.
  VRMUtils.rotateVRM0(vrm);
  return vrm;
}

// Reintenta fallos transitorios (disco/decoder); null tras agotar intentos.
async function loadVrm(vrmPath) {
  for (let attempt = 1; attempt <= VRM_LOAD_MAX_ATTEMPTS; attempt++) {
    try {
      return await loadVrmOnce(vrmPath);
    } catch (err) {
      console.error(
        `[renderer] VRM intento ${attempt}/${VRM_LOAD_MAX_ATTEMPTS} falló (${vrmPath}):`,
        err,
      );
      if (attempt === VRM_LOAD_MAX_ATTEMPTS) return null;
      await sleep(VRM_LOAD_RETRY_BASE_MS * attempt);
    }
  }
  return null;
}

// Avatar = vrm + su mixer + idle + caché de clips (un clip VRMA está atado a UN vrm).
async function buildAvatar(vrmPath) {
  const vrm = await loadVrm(vrmPath);
  if (!vrm) return null;
  state.scene.add(vrm.scene);
  vrm.scene.position.set(0, 0, 0);
  // Oculto hasta que el idle evalúe su primer frame (evita el flash de T-pose).
  vrm.scene.visible = false;
  const avatar = {
    vrm,
    mixer: new THREE.AnimationMixer(vrm.scene),
    idleAction: null,
    clipCache: new Map(),
  };
  await loadIdleInto(avatar);
  return avatar;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function loadIdleInto(avatar) {
  try {
    const clip = await loadClipFor(avatar, animPath(IDLE_CLIP_REL));
    if (!clip) return;
    const action = avatar.mixer.clipAction(clip);
    action.setLoop(THREE.LoopRepeat, Infinity);
    avatar.idleAction = action;
    // Al terminar un gesto, volver al idle.
    avatar.mixer.addEventListener("finished", (ev) => {
      if (ev.action === state.emoteAction) returnToIdle();
    });
  } catch (err) {
    console.warn("[renderer] no se pudo cargar el idle:", err);
  }
}

function adoptAvatar(avatar) {
  state.avatar = avatar;
  state.vrm = avatar.vrm;
  state.mixer = avatar.mixer;
  state.idleAction = avatar.idleAction;
}

// ---------------------------------------------------------------------------
// Gestos
// ---------------------------------------------------------------------------
async function loadClipFor(avatar, vrmaPath) {
  if (!avatar) return null;
  if (avatar.clipCache.has(vrmaPath)) return avatar.clipCache.get(vrmaPath);
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
  const gltf = await loader.loadAsync(vrmaPath);
  const vrmAnimations = gltf.userData.vrmAnimations;
  if (!vrmAnimations || vrmAnimations.length === 0) {
    throw new Error(`sin animaciones en ${vrmaPath}`);
  }
  const clip = createVRMAnimationClip(vrmAnimations[0], avatar.vrm);
  avatar.clipCache.set(vrmaPath, clip);
  return clip;
}

// Gesto de un solo disparo con crossfade desde el gesto/idle en curso.
async function playEmote(vrmaPath) {
  if (!state.vrm || !state.mixer) return;
  let clip;
  try {
    clip = await loadClipFor(state.avatar, vrmaPath);
  } catch (err) {
    console.warn("[renderer] no se pudo cargar el gesto:", vrmaPath, err);
    return;
  }
  if (!clip) return;

  const previous = state.emoteAction;
  const action = state.mixer.clipAction(clip);
  action.reset();
  action.setLoop(THREE.LoopOnce, 1);
  action.clampWhenFinished = true;
  state.emoteAction = action;

  ensureAvatarVisible();
  const from =
    previous && previous.isRunning()
      ? previous
      : state.idleAction && state.idleAction.isRunning()
        ? state.idleAction
        : null;
  if (from) from.crossFadeTo(action, CROSSFADE_SECONDS, false);
  action.play();
}

function returnToIdle() {
  if (!state.idleAction || !state.emoteAction) return;
  state.idleAction.reset();
  state.idleAction.play();
  state.emoteAction.crossFadeTo(state.idleAction, CROSSFADE_SECONDS, false);
  state.emoteAction = null;
}

// Revela el avatar; los dos micro-updates fuerzan el frame t=0 del clip ANTES del
// primer render (sin flash de T-pose).
function ensureAvatarVisible() {
  if (!state.vrm || state.vrm.scene.visible) return;
  if (state.idleAction && !state.emoteAction) {
    state.idleAction.reset();
    state.idleAction.play();
  }
  state.mixer.update(0.001);
  state.vrm.update(0.001);
  state.vrm.scene.visible = true;
}

// ---------------------------------------------------------------------------
// Render loop (mixer + vrm + labio + subtítulos)
// ---------------------------------------------------------------------------
function startRenderLoop() {
  const tick = () => {
    const dt = state.clock.getDelta();
    if (state.mixer) state.mixer.update(dt);
    if (state.vrm) {
      state.vrm.update(dt);
      updateLipsync();
    }
    revealCaptions(dt);
    state.renderer.render(state.scene, state.camera);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function revealCaptions(dt) {
  revealAssistantBubble(dt);
  revealUserBubbleInstant();
}

// Norivy: revelado tipo máquina de escribir a ritmo de habla; el gesto se dispara
// con lo REVELADO (acompaña el audio). Sella al completar.
function revealAssistantBubble(dt) {
  const b = state.bubbles.assistant;
  if (!b || !b.el) return;
  const full = b.full || "";
  const shownChars = b.shownChars || 0;
  if (shownChars < full.length) {
    b.shownChars = advanceReveal(shownChars, full.length, dt, CAPTION_CHARS_PER_SEC);
    const shown = full.slice(0, Math.floor(b.shownChars));
    setBubbleText("assistant", shown);
    if (state.emoteEngine) state.emoteEngine.feed(shown);
  }
  if (b.ended && (b.shownChars || 0) >= full.length) sealActiveBubble("assistant");
}

// Cliente: instantáneo (la transcripción llega cuando YA terminó de hablar).
function revealUserBubbleInstant() {
  const b = state.bubbles.user;
  if (!b || !b.el) return;
  const full = b.full || "";
  if ((b.shownChars || 0) < full.length) {
    b.shownChars = full.length;
    setBubbleText("user", full);
  }
  if (b.ended && (b.shownChars || 0) >= full.length) sealActiveBubble("user");
}

function updateLipsync() {
  const expr = state.vrm.expressionManager;
  if (state.analyser && state.analyserData) {
    state.analyser.getByteTimeDomainData(state.analyserData);
    let sum = 0;
    for (let i = 0; i < state.analyserData.length; i++) {
      const v = (state.analyserData[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / state.analyserData.length);
    state.mouthAmp = rmsToMouthAmp(rms, {
      gain: LIPSYNC_GAIN,
      prevAmp: state.mouthAmp,
      smoothing: LIPSYNC_SMOOTHING,
    });
  } else {
    state.mouthAmp *= MOUTH_DECAY;
  }
  if (expr) {
    try {
      expr.setValue("aa", state.mouthAmp);
    } catch {
      // Algunos VRM 0.x usan otros nombres de expresión; se ignora.
    }
  }
}

// ---------------------------------------------------------------------------
// Audio (PCM16 24 kHz en cola programada, sin cortes)
// ---------------------------------------------------------------------------
function ensureAudioContext() {
  if (!state.audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    state.audioCtx = new Ctx({ sampleRate: AUDIO_SAMPLE_RATE });
    state.analyser = state.audioCtx.createAnalyser();
    state.analyser.fftSize = 512;
    state.analyserData = new Uint8Array(state.analyser.frequencyBinCount);
    state.analyser.connect(state.audioCtx.destination);
  }
  if (state.audioCtx.state === "suspended") {
    state.audioCtx.resume().catch((err) => {
      console.warn("[renderer] audioCtx resume falló:", err);
    });
  }
  return state.audioCtx;
}

function enqueueAudio(base64Pcm) {
  const ctx = ensureAudioContext();
  const int16 = base64ToInt16(base64Pcm);
  if (int16.length === 0) return;
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
  const buffer = ctx.createBuffer(1, float32.length, AUDIO_SAMPLE_RATE);
  buffer.copyToChannel(float32, 0);

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(state.analyser);
  state.scheduledSources.add(source);
  source.onended = () => state.scheduledSources.delete(source);

  const when = computeStartTime(state.nextStartTime, ctx.currentTime);
  source.start(when);
  state.nextStartTime = when + buffer.duration;
}

// Corta todo el audio ya programado (el cliente interrumpe / se cierra la sesión).
function stopScheduledAudio() {
  for (const source of state.scheduledSources) {
    try {
      source.onended = null;
      source.stop();
      source.disconnect();
    } catch {
      // La fuente ya terminó; nada que detener.
    }
  }
  state.scheduledSources.clear();
  if (state.audioCtx) resetAudioSchedule();
}

function resetAudioSchedule() {
  const ctx = ensureAudioContext();
  state.nextStartTime = ctx.currentTime + 0.03;
}

// ---------------------------------------------------------------------------
// WebSocket — hub local (PROTOCOLO COMPARTIDO con el backend)
// ---------------------------------------------------------------------------
function connectWs(url) {
  try {
    const ws = new WebSocket(url);
    state.ws = ws;
    ws.addEventListener("open", () => {
      state.reconnectDelay = WS_RECONNECT_MIN_MS;
      setStatus("connected");
    });
    ws.addEventListener("message", (ev) => handleMessage(ev.data));
    ws.addEventListener("close", () => {
      setStatus("offline");
      scheduleReconnect(url);
    });
    ws.addEventListener("error", () => setStatus("error"));
  } catch (err) {
    console.error("[renderer] error de conexión WS:", err);
    scheduleReconnect(url);
  }
}

function scheduleReconnect(url) {
  if (state.reconnectTimer) return;
  const jitter = Math.random() * 250;
  const delay = state.reconnectDelay + jitter;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, WS_RECONNECT_MAX_MS);
    connectWs(url);
  }, delay);
}

function sendWs(payload) {
  const sock = state.ws;
  if (!sock || sock.readyState !== WebSocket.OPEN) {
    console.warn("[renderer] WS cerrado, descarto:", payload.type);
    return false;
  }
  sock.send(JSON.stringify(payload));
  return true;
}

// Frames que cuentan como actividad del cliente (reinician el reloj de inactividad).
const ACTIVITY_FRAMES = new Set([
  "user_speech_started",
  "speech_started",
  "product_results",
  "applicability",
  "quote_cart",
  "quote_ready",
]);

function handleMessage(raw) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  if (ACTIVITY_FRAMES.has(msg.type)) markActivity();
  switch (msg.type) {
    case "hello":
      onHello(msg);
      break;
    case "status":
      setStatus(msg.state || "", msg.detail);
      break;
    case "speech_started":
      onSpeechStarted();
      break;
    case "audio":
      if (msg.pcm16) enqueueAudio(msg.pcm16);
      break;
    case "transcript":
      onTranscript(msg.delta || "");
      break;
    case "user_transcript":
      onUserTranscript(msg.delta || "");
      break;
    case "user_transcript_done":
      onUserTranscriptDone(msg.transcript || "");
      break;
    case "user_speech_started":
      onUserSpeechStarted();
      break;
    case "speech_done":
      onSpeechDone();
      break;
    case "mic_state":
      setMicBadge(!!msg.muted);
      break;
    case "session_reset":
      closeSessionLocally();
      break;
    case "product_results":
    case "applicability":
    case "quote_cart":
    case "quote_ready":
      state.view = applyFrame(state.view, msg);
      render();
      break;
    case "error":
      console.warn("[renderer] error del backend:", msg.message);
      setStatus("error", msg.message);
      break;
    default:
      break;
  }
}

function onHello(msg) {
  if (msg.character_name) state.characterName = msg.character_name;
  if (msg.store_name) state.storeName = msg.store_name;
  renderBranding();
  setMicBadge(!!msg.mic_muted);
  // El backend puede estar arriba pero sin OpenAI (clave/red): no mentir «Conectada».
  if (msg.realtime_active === false) {
    setStatus("fatal", "Sin conexión con OpenAI");
  } else {
    setStatus("connected");
  }
}

function onSpeechStarted() {
  state.norivySpeaking = true;
  if (state.emoteEngine) state.emoteEngine.reset();
  resetAudioSchedule();
  startNewBubble("assistant");
}

function onTranscript(delta) {
  if (!delta) return;
  if (!state.bubbles.assistant.el) startNewBubble("assistant");
  const b = state.bubbles.assistant;
  b.full = (b.full || "") + delta;
}

// speech_done llega cuando OpenAI TERMINÓ de generar, mucho antes de que el audio
// termine de sonar: solo marcamos que no vendrán más deltas; el tipeo sigue a ritmo
// de habla y revealAssistantBubble sella la burbuja al terminar de revelar.
function onSpeechDone() {
  const b = state.bubbles.assistant;
  if (b && b.el) b.ended = true;
  state.norivySpeaking = false;
}

function onUserSpeechStarted() {
  // El cliente interrumpe: cortamos YA el audio bufferizado de Norivy.
  stopScheduledAudio();
  if (!state.bubbles.user.el) startNewBubble("user");
}

function onUserTranscript(delta) {
  if (!delta) return;
  if (!state.bubbles.user.el) startNewBubble("user");
  const b = state.bubbles.user;
  b.full = (b.full || "") + delta;
}

function onUserTranscriptDone(transcript) {
  const b = state.bubbles.user;
  if (!b || !b.el) return;
  const clean = (transcript || "").trim();
  if (clean) {
    b.full = clean;
    b.ended = true;
  } else if (!(b.full || "")) {
    // No se transcribió nada: fuera el placeholder.
    b.el.remove();
    state.bubbles.user = { el: null, text: "", full: "", shownChars: 0, ended: false };
  } else {
    b.ended = true;
  }
}

// ---------------------------------------------------------------------------
// Burbujas de subtítulos (en la columna del avatar)
// ---------------------------------------------------------------------------
const ROLE_META = {
  assistant: { cls: "assistant", name: () => state.characterName },
  user: { cls: "user", name: () => "Usted" },
};

function getChatFeed() {
  return document.getElementById("chat-feed");
}

function startNewBubble(role) {
  const feed = getChatFeed();
  if (!feed) return;
  feed.querySelectorAll(".chat-bubble").forEach((b) => b.classList.remove("live"));
  const meta = ROLE_META[role] || ROLE_META.assistant;
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${meta.cls} live`;
  const name = document.createElement("div");
  name.className = "chat-name";
  name.textContent = meta.name();
  const text = document.createElement("div");
  text.className = "chat-text";
  text.textContent = "…";
  bubble.append(name, text);
  feed.appendChild(bubble);
  state.bubbles[role] = { el: bubble, text: "", full: "", shownChars: 0, ended: false };
  trimBubbles();
}

function setBubbleText(role, text) {
  const b = state.bubbles[role];
  if (!b || !b.el) return;
  b.text = text;
  const textEl = b.el.querySelector(".chat-text");
  if (textEl) textEl.textContent = text;
}

function sealActiveBubble(role) {
  const b = state.bubbles[role];
  if (!b || !b.el) return;
  b.el.classList.remove("live");
  b.el.classList.add("done");
  state.bubbles[role] = { el: null, text: "", full: "", shownChars: 0, ended: false };
}

function trimBubbles() {
  const feed = getChatFeed();
  if (!feed) return;
  const bubbles = feed.querySelectorAll(".chat-bubble");
  for (let i = 0; i < bubbles.length - MAX_BUBBLES; i++) bubbles[i].remove();
}

function clearBubbles() {
  const feed = getChatFeed();
  if (feed) feed.replaceChildren();
  state.bubbles = {
    assistant: { el: null, text: "" },
    user: { el: null, text: "" },
  };
}

// ---------------------------------------------------------------------------
// Estado del sistema (pill solo para problemas) + mic
// ---------------------------------------------------------------------------
const STATUS_LABELS = {
  connecting: "Conectando…",
  connected: "Conectada",
  listening: "Escuchando",
  speaking: "Hablando",
  working: "Consultando…",
  offline: "Sin conexión",
  reconnecting: "Reconectando…",
  error: "Error",
  fatal: "Sin conexión con OpenAI",
  mic_error: "Micrófono no disponible",
  asset_error: "No se pudo cargar el avatar",
};

function setStatus(stateKey, detail) {
  const pill = document.getElementById("status-pill");
  if (!pill) return;
  // Un estado crítico no lo pisa un «connected» rutinario (ver statusMachine).
  if (!canApplyStatus(pill.dataset.state, stateKey)) return;
  const label = STATUS_LABELS[stateKey] || stateKey;
  pill.textContent = detail ? `${label}: ${detail}` : label;
  pill.dataset.state = stateKey;
  // «Consultando…» se muestra al cliente como indicador de que Norivy busca.
  document.body.classList.toggle("is-working", stateKey === "working");
}

function setMicBadge(muted) {
  document.body.classList.toggle("mic-live", !muted);
  // Mic vivo confirmado = el mic se recuperó: sacamos el sticky mic_error.
  if (!muted) clearMicErrorPill();
}

function clearMicErrorPill() {
  const pill = document.getElementById("status-pill");
  if (!pill || pill.dataset.state !== "mic_error") return;
  pill.dataset.state = "connected";
  setStatus("connected");
}

// ---------------------------------------------------------------------------
// Sesión de atención + inactividad
// ---------------------------------------------------------------------------
function markActivity() {
  state.lastActivity = Date.now();
  document.body.classList.remove("idle-warning");
}

function beginSession() {
  const pill = document.getElementById("status-pill");
  if (!canStartSession(pill ? pill.dataset.state : "")) return;
  if (!sendWs({ type: "start" })) return;
  ensureAudioContext(); // el toque es el gesto de usuario que desbloquea el audio
  state.view = startSession();
  markActivity();
  ensureAvatarVisible();
  if (state.emoteEngine) state.emoteEngine.playGreeting();
  render();
}

// Fin de atención pedido por el cliente (botón) o por inactividad.
function endSession() {
  sendWs({ type: "reset" });
  closeSessionLocally();
}

// Deja la pantalla lista para el siguiente cliente (idempotente: también la
// dispara el frame session_reset del backend).
function closeSessionLocally() {
  stopScheduledAudio();
  state.norivySpeaking = false;
  clearBubbles();
  state.view = initialView();
  document.body.classList.remove("idle-warning");
  render();
}

function checkIdle() {
  if (state.view.session !== "active") return;
  // Mientras Norivy habla (generando o sonando) el cliente está siendo atendido.
  if (state.norivySpeaking || state.scheduledSources.size > 0) {
    markActivity();
    return;
  }
  const extra = state.view.main === "quote_ready" ? IDLE_QUOTE_EXTRA_MS : 0;
  const step = idleStep(Date.now() - state.lastActivity, {
    warnAfterMs: IDLE_WARN_MS + extra,
    resetAfterMs: IDLE_RESET_MS + extra,
  });
  if (step === "reset") endSession();
  else if (step === "warn") document.body.classList.add("idle-warning");
}

function wireUi() {
  document.getElementById("attract").addEventListener("pointerdown", beginSession);
  document.getElementById("btn-end").addEventListener("pointerdown", endSession);
  document.getElementById("btn-continue").addEventListener("pointerdown", markActivity);
  // Cualquier toque cuenta como actividad.
  window.addEventListener("pointerdown", markActivity, { capture: true });
}

// ---------------------------------------------------------------------------
// Pintado de la vista (SOLO textContent: los datos vienen de la API de la tienda)
// ---------------------------------------------------------------------------
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderBranding() {
  const store = document.getElementById("store-name");
  if (store) store.textContent = state.storeName || "";
}

function render() {
  const v = state.view;
  document.body.dataset.session = v.session;
  document.body.dataset.main = v.main;
  renderProducts(v.products);
  renderApplicability(v.applicability);
  renderCart(v.cart);
  renderQuote(v.quote);
}

function productCard(p) {
  const card = el("article", "card");
  const imageUrl = safeHttpUrl(p.image_url);
  if (imageUrl) {
    const img = el("img", "card-img");
    img.src = imageUrl;
    img.alt = "";
    img.loading = "lazy";
    card.appendChild(img);
  }
  card.appendChild(el("div", "card-kind", [p.kind, p.brand].filter(Boolean).join(" · ")));
  card.appendChild(el("h3", "card-name", p.name || ""));
  const stock = stockLabel(p.stock);
  const foot = el("div", "card-foot");
  foot.appendChild(el("span", "card-price", formatPrice(p.price, p.currency)));
  const badge = el("span", `badge badge-${stock.level}`, stock.text);
  foot.appendChild(badge);
  card.appendChild(foot);
  return card;
}

function renderProducts(products) {
  const grid = document.getElementById("products-grid");
  const empty = document.getElementById("products-empty");
  grid.replaceChildren(...products.map(productCard));
  empty.hidden = products.length > 0;
}

function renderApplicability(app) {
  const title = document.getElementById("applicability-title");
  const list = document.getElementById("applicability-list");
  if (!app) {
    title.textContent = "";
    list.replaceChildren();
    return;
  }
  title.textContent = app.productName;
  if (app.items.length === 0) {
    list.replaceChildren(el("li", "fit fit-none", "No hay motos registradas para este producto."));
    return;
  }
  list.replaceChildren(
    ...app.items.map((f) => {
      const li = el("li", "fit");
      li.appendChild(el("span", "fit-model", [f.brand, f.model].filter(Boolean).join(" ")));
      const years = yearRange(f.year_from, f.year_to);
      if (years) li.appendChild(el("span", "fit-years", years));
      if (f.notes) li.appendChild(el("span", "fit-notes", f.notes));
      return li;
    }),
  );
}

function renderCart(cart) {
  const rows = document.getElementById("cart-rows");
  const total = document.getElementById("cart-total");
  rows.replaceChildren(
    ...cart.items.map((i) => {
      const row = el("li", "cart-row");
      row.appendChild(el("span", "cart-qty", `${i.quantity}×`));
      row.appendChild(el("span", "cart-name", i.name || ""));
      row.appendChild(el("span", "cart-price", formatPrice(i.price * i.quantity, i.currency)));
      return row;
    }),
  );
  total.textContent = formatPrice(cart.total, cart.items[0] ? cart.items[0].currency : "USD");
  document.body.classList.toggle("has-cart", cart.items.length > 0);
}

function renderQuote(quote) {
  const qr = document.getElementById("quote-qr");
  const total = document.getElementById("quote-total");
  if (!quote) {
    qr.replaceChildren();
    total.textContent = "";
    return;
  }
  renderQr(qr, quote.url);
  total.textContent = formatPrice(quote.total, quote.currency);
}

// ---------------------------------------------------------------------------
bootstrap().catch((err) => console.error("[renderer] bootstrap falló:", err));
