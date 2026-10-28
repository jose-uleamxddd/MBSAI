"""TDD FIX 6 — `realtime_active` unificado entre /healthz y el frame `hello`.

Comentario en español: antes /healthz reportaba `realtime_active` con una lógica
(`_active` a secas) y `hello()` con otra (`_active AND client.connected`) → el
operador (healthz) y el renderer (hello) veían VERDADES DISTINTAS sobre si OpenAI
está conectado. Debe haber UNA propiedad canónica usada por ambos. La red se
INYECTA (cliente fake) — no tocamos OpenAI real ni levantamos un TestClient.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from norivy_kiosk.app import create_app
from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime.session import RealtimeSession


class _StubClient:
    """Cliente fake con `connected` scriptable (sin red)."""

    def __init__(self, *, connected: bool) -> None:
        self.connected = connected
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @property
    def events(self) -> asyncio.Queue[dict[str, Any]]:
        return self._events


def _find_route(app: FastAPI, path: str) -> APIRoute:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route
    raise AssertionError(f"ruta {path} no encontrada")


@pytest.mark.unit
def test_realtime_active_true_solo_con_active_y_conectado() -> None:
    # active pero OpenAI desconectado → NO está realmente activo.
    session = RealtimeSession(Hub(), client=_StubClient(connected=False))
    session._active = True
    assert session.realtime_active is False

    # active + conectado → activo de verdad.
    session_ok = RealtimeSession(Hub(), client=_StubClient(connected=True))
    session_ok._active = True
    assert session_ok.realtime_active is True


@pytest.mark.unit
def test_hello_usa_la_propiedad_canonica() -> None:
    session = RealtimeSession(Hub(), client=_StubClient(connected=False))
    session._active = True  # active pero sin OpenAI: hello NO debe mentir "activo".
    assert session.hello()["realtime_active"] == session.realtime_active
    assert session.hello()["realtime_active"] is False


@pytest.mark.integration
async def test_healthz_y_hello_coinciden_en_realtime_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: interceptamos la sesión que crea create_app para forzar el estado
    # "active pero OpenAI caído" (el caso donde antes divergían).
    captured: dict[str, RealtimeSession] = {}
    real_init = RealtimeSession.__init__

    def spy_init(self: RealtimeSession, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        captured["session"] = self

    monkeypatch.setattr(RealtimeSession, "__init__", spy_init)
    app = create_app()
    session = captured["session"]
    session._client = _StubClient(connected=False)  # type: ignore[assignment]
    session._active = True

    # Act: llamamos al handler de /healthz directamente (sin TestClient/httpx).
    healthz = _find_route(app, "/healthz").endpoint
    payload = await healthz()

    # Assert: /healthz y hello reportan el MISMO realtime_active.
    assert payload["realtime_active"] == session.hello()["realtime_active"]
    assert payload["realtime_active"] is False  # active pero OpenAI caído
