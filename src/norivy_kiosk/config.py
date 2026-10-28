"""Configuración del backend — lee `.env` vía python-dotenv.

Comentario en español: única fuente de verdad de red, identidad, parámetros de
voz y conexión con la API de la tienda. Obligatorios: `OPENAI_API_KEY` y las
credenciales del usuario de servicio de la tienda; el resto tiene defaults.
El system prompt se arma con `persona.build_system_prompt`.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from . import persona

load_dotenv()


def _resolve_mic_device(raw: str) -> str | int | None:
    """Convierte el override de mic a índice int, nombre str o None (default)."""
    value = raw.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return value


# --- Identidad del asistente y de la tienda ---
CHARACTER_NAME: str = os.environ.get("NORIVY_KIOSK_CHARACTER_NAME", "Norivy")
STORE_NAME: str = os.environ.get("NORIVY_KIOSK_STORE_NAME", "nuestra tienda")

# --- Red local (consumida por Electron) ---
HOST: str = os.environ.get("NORIVY_KIOSK_HOST", "127.0.0.1")
# 8771 = stage-v2, 8772 = desktop → el kiosco usa 8773 para no chocar.
PORT: int = int(os.environ.get("NORIVY_KIOSK_PORT", "8773"))

# --- OpenAI Realtime GA ---
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
MODEL: str = os.environ.get("NORIVY_KIOSK_MODEL", "gpt-realtime-2.1")
VOICE: str = os.environ.get("NORIVY_KIOSK_VOICE", "coral")
OUTPUT_SPEED: float = float(os.environ.get("NORIVY_KIOSK_OUTPUT_SPEED", "1.05"))
# Modelo de transcripción del mic (lo que dice el cliente) para el subtítulo en vivo.
INPUT_TRANSCRIBE_MODEL: str = os.environ.get(
    "NORIVY_KIOSK_INPUT_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"
)
# Detector de voz. "server_vad" (default) tiene un THRESHOLD que rechaza el ruido
# de fondo de la tienda (música, motores, otros clientes); "semantic_vad" detecta
# cualquier voz por significado y queda para salas silenciosas.
VAD_MODE: str = os.environ.get("NORIVY_KIOSK_VAD_MODE", "server_vad")
# Umbral de server_vad (0-1): más alto = exige voz más fuerte y cercana. Se calibra
# en el local con el micrófono real.
VAD_THRESHOLD: float = float(os.environ.get("NORIVY_KIOSK_VAD_THRESHOLD", "0.6"))
VAD_PREFIX_PADDING_MS: int = int(os.environ.get("NORIVY_KIOSK_VAD_PREFIX_MS", "300"))
VAD_SILENCE_DURATION_MS: int = int(os.environ.get("NORIVY_KIOSK_VAD_SILENCE_MS", "700"))
# Sensibilidad del semantic_vad (low|medium|high|auto). Solo con VAD_MODE=semantic_vad.
VAD_EAGERNESS: str = os.environ.get("NORIVY_KIOSK_VAD_EAGERNESS", "low")
# Reducción de ruido de OpenAI ANTES del detector: "near_field" (cliente frente a
# la pantalla), "far_field" (mic lejano) u "off".
NOISE_REDUCTION: str = os.environ.get("NORIVY_KIOSK_NOISE_REDUCTION", "far_field")
REALTIME_URL: str = "wss://api.openai.com/v1/realtime"

# --- Resiliencia de red (reconexión + keepalive al WS de OpenAI) ---
RECONNECT_MIN_S: float = 0.5
RECONNECT_MAX_S: float = 8.0
CONNECT_TIMEOUT_S: float = 10.0
PING_INTERVAL_S: float = 20.0
PING_TIMEOUT_S: float = 20.0
EVENTS_QUEUE_MAX: int = 512
# Fracción de cuota restante bajo la cual avisar al operador ANTES del fatal.
RATE_LIMIT_WARN_REMAINING: float = 0.15
# Intervalo mínimo entre inicios de sesión (toque en pantalla): freno anti-spam.
GREET_MIN_INTERVAL_S: float = 3.0

# --- Audio (PCM16 mono 24 kHz en ambas direcciones) ---
MIC_SAMPLE_RATE: int = 24000
MIC_CHANNELS: int = 1
MIC_DEVICE: str | int | None = _resolve_mic_device(
    os.environ.get("NORIVY_KIOSK_MIC_DEVICE", "")
)
MIC_QUEUE_MAX: int = 48
MIC_WATCHDOG_INTERVAL_S: float = 4.0
# Half-duplex (parlantes abiertos): margen extra tras el fin estimado del audio de
# Norivy antes de reabrir el mic. Subir si se filtra feedback.
HALF_DUPLEX_TAIL_S: float = 0.8

# --- API de la tienda (usuario de servicio "Norivy") ---
# Las credenciales viven SOLO en el backend: nunca llegan al renderer ni a OpenAI.
STORE_API_URL: str = os.environ.get("NORIVY_KIOSK_STORE_URL", "").rstrip("/")
STORE_API_USER: str = os.environ.get("NORIVY_KIOSK_STORE_USER", "")
STORE_API_PASSWORD: str = os.environ.get("NORIVY_KIOSK_STORE_PASSWORD", "")
STORE_API_TIMEOUT_S: float = float(os.environ.get("NORIVY_KIOSK_STORE_TIMEOUT_S", "8"))
# Margen antes del vencimiento del accessToken para renovarlo de forma proactiva.
STORE_TOKEN_SKEW_S: float = 30.0
# Máximo de productos que el asistente recibe/muestra por búsqueda.
SEARCH_MAX_RESULTS: int = 6

# --- System prompt de Norivy (persona + reglas de la tienda) ---
SYSTEM_PROMPT: str = persona.build_system_prompt(CHARACTER_NAME, STORE_NAME)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_host(host: str) -> bool:
    """True si el host es loopback (el WS local SIN auth solo es seguro ahí)."""
    return host.strip().lower() in _LOOPBACK_HOSTS


def require_api_key() -> str:
    """Devuelve la API key o levanta un error claro si falta al arrancar."""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "Falta OPENAI_API_KEY. Defínela en el entorno o en un archivo .env "
            "(copia env.example a .env y complétala) antes de arrancar el backend."
        )
    return OPENAI_API_KEY


def require_store_credentials() -> tuple[str, str, str]:
    """Devuelve (url, usuario, contraseña) de la tienda o falla con un error claro."""
    if not (STORE_API_URL and STORE_API_USER and STORE_API_PASSWORD):
        raise RuntimeError(
            "Faltan NORIVY_KIOSK_STORE_URL / NORIVY_KIOSK_STORE_USER / "
            "NORIVY_KIOSK_STORE_PASSWORD en el .env (usuario de servicio de Norivy)."
        )
    return STORE_API_URL, STORE_API_USER, STORE_API_PASSWORD
