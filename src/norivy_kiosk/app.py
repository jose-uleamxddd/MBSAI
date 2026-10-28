"""FastAPI app — /healthz + WS /ws/kiosk; arranca la sesión Realtime al boot.

Comentario en español: el backend solo expone API + WebSocket. El renderer
Electron carga los assets locales por su cuenta. Toda la coordinación va por el
WS: el renderer manda ``start``/``reset`` y recibe el protocolo compartido.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from . import config
from .hub import Hub
from .realtime.session import RealtimeSession

# Fábrica de la sesión Realtime a partir del Hub. Inyectable SOLO para tests
# (una sesión fake sin red/mic); en producción queda en el default y arma el
# RealtimeSession real — el runtime no cambia.
SessionFactory = Callable[[Hub], RealtimeSession]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(*, session_factory: SessionFactory | None = None) -> FastAPI:
    """Construye la app: instancia Hub + RealtimeSession y cablea las rutas.

    ``session_factory`` es un punto de inyección para tests: recibe el Hub y
    devuelve la sesión. Si es ``None`` (producción), se arma el
    ``RealtimeSession`` real — el arranque por ``python -m norivy_kiosk`` no
    cambia.
    """
    hub = Hub()
    session = (session_factory or RealtimeSession)(hub)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not config.is_loopback_host(config.HOST):
            logger.warning(
                "[app] NORIVY_KIOSK_HOST=%s NO es loopback — el WS /ws/kiosk queda "
                "EXPUESTO a la red SIN auth. Usa 127.0.0.1 salvo que sepas por qué.",
                config.HOST,
            )
        try:
            config.require_api_key()
            await session.start()
        except Exception as exc:  # noqa: BLE001 — boundary de arranque
            logger.error(
                "[app] no se pudo arrancar la sesión Realtime: %s "
                "(el server sigue vivo para /healthz)",
                exc,
            )
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                await session.stop()

    app = FastAPI(title="norivy-kiosk", version="1.0.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "model": config.MODEL,
            "voice": config.VOICE,
            "mic_muted": session.mic_muted,
            "realtime_active": session.realtime_active,
            "openai_connected": session.openai_connected,
            "mic_alive": session.mic_alive,
        }

    @app.websocket("/ws/kiosk")
    async def ws_kiosk(ws: WebSocket) -> None:
        await ws.accept()
        await hub.register(ws)
        try:
            await ws.send_json(session.hello())
            while True:
                message = await ws.receive_json()
                await _dispatch(session, message)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001 — boundary del WS
            logger.warning("[ws] error en /ws/kiosk: %s", exc)
        finally:
            await hub.unregister(ws)

    return app


async def _dispatch(session: RealtimeSession, message: dict[str, Any]) -> None:
    """Despacha los mensajes de control del renderer (start/reset)."""
    msg_type = message.get("type")
    if msg_type == "start":
        await session.begin_session()
    elif msg_type == "reset":
        await session.end_session()
    else:
        logger.debug("[ws] mensaje de control desconocido: %s", msg_type)
