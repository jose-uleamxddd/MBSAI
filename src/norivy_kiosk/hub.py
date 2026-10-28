"""Hub — broadcast del WebSocket local a todos los renderers conectados.

Comentario en español: punto único donde el backend se entera del/los
renderer(s) Electron. La sesión Realtime publica acá y el Hub reparte el mismo
frame JSON a cada cliente, descartando los que se desconectaron a mitad de envío.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Hub:
    """Mantiene los WebSockets vivos y broadcasta eventos a todos."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
        logger.info("[hub] cliente conectado (%d total)", len(self._clients))

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.info("[hub] cliente desconectado (%d total)", len(self._clients))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Manda el frame JSON a todos EN PARALELO; descarta clientes que fallan.

        El lock solo protege el set de clientes (snapshot + limpieza), NUNCA el
        I/O de red: un cliente lento no debe frenar el broadcast a los demás
        (sin head-of-line blocking).
        """
        if not self._clients:
            return
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients)
        results = await asyncio.gather(
            *(self._safe_send(ws, payload, message) for ws in clients)
        )
        stale = [ws for ws, ok in zip(clients, results, strict=True) if not ok]
        if stale:
            async with self._lock:
                for ws in stale:
                    self._clients.discard(ws)

    async def _safe_send(
        self, ws: WebSocket, payload: str, message: dict[str, Any]
    ) -> bool:
        """Envía a un cliente; devuelve False si falla (para descartarlo luego)."""
        try:
            await ws.send_text(payload)
            return True
        except Exception as exc:  # noqa: BLE001 — boundary del WS
            logger.warning(
                "[hub] send_text falló (%s): %s — descarto cliente",
                message.get("type", "?"),
                exc,
            )
            return False
