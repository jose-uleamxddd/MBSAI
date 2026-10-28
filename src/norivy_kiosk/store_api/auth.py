"""Token de acceso del usuario de servicio «Norivy» contra la API de la tienda.

Comentario en español: hace login con las credenciales del `.env`, guarda el
accessToken EN MEMORIA y lo renueva (refreshToken si hay, si no login de nuevo)
poco antes de que venza. Un lock evita que dos tools concurrentes hagan login a
la vez. Las rutas y los nombres de campo de la API viven SOLO en este archivo y en
``client.py``: cuando llegue la documentación real, se ajusta acá.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LOGIN_PATH = "/auth/login"
REFRESH_PATH = "/auth/refresh"
# Si la API no informa expiración, asumimos una vida corta y renovamos seguido.
DEFAULT_EXPIRES_IN_S = 600.0


class AuthError(Exception):
    """No se pudo obtener un token (credenciales malas o API caída)."""


def pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Primer valor presente entre varias convenciones de nombre (camel/snake)."""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


class TokenManager:
    """Obtiene y renueva el accessToken; ``get()`` siempre devuelve uno vigente."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        user: str,
        password: str,
        skew_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._user = user
        self._password = password
        self._skew_s = skew_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._access: str | None = None
        self._refresh: str | None = None
        self._expires_at = 0.0

    def _valid(self) -> bool:
        return self._access is not None and self._clock() < self._expires_at

    async def get(self) -> str:
        """Token vigente; renueva (o hace login) si falta o está por vencer."""
        if self._valid():
            return self._access or ""
        async with self._lock:
            if self._valid():  # otro corrutina lo renovó mientras esperábamos
                return self._access or ""
            if not (self._refresh and await self._try_refresh()):
                await self._login()
            return self._access or ""

    def invalidate(self) -> None:
        """Descarta el accessToken (p. ej. tras un 401); conserva el refreshToken."""
        self._access = None
        self._expires_at = 0.0

    async def _login(self) -> None:
        body = {"email": self._user, "password": self._password}
        self._store(await self._post(LOGIN_PATH, body))
        logger.info("[store] login OK")

    async def _try_refresh(self) -> bool:
        try:
            self._store(await self._post(REFRESH_PATH, {"refreshToken": self._refresh}))
        except AuthError:
            self._refresh = None  # refresh vencido/inválido → caer a login
            return False
        logger.info("[store] token renovado")
        return True

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise AuthError(f"API de la tienda no disponible: {exc}") from exc
        if resp.status_code >= 400:
            raise AuthError(f"auth {path} → HTTP {resp.status_code}")
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def _store(self, data: dict[str, Any]) -> None:
        access = pick(data, "accessToken", "access_token", "token")
        if not isinstance(access, str) or not access:
            raise AuthError("la API no devolvió accessToken")
        self._access = access
        self._refresh = pick(data, "refreshToken", "refresh_token") or self._refresh
        expires_in = float(
            pick(data, "expiresIn", "expires_in", default=DEFAULT_EXPIRES_IN_S)
        )
        self._expires_at = self._clock() + max(expires_in - self._skew_s, 0.0)
