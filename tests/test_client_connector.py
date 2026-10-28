"""TDD — `_default_connector`: connector de producción del WS de OpenAI.

Comentario en español: `_default_connector` es el connector REAL (el que en
runtime abre el WebSocket GA de OpenAI). En el resto de la suite se inyecta un
fake, así que este bloque nunca corría. Acá se mockea `websockets` en
`sys.modules` para ejercitar (a) el camino normal (`additional_headers`,
websockets 13+) y (b) el fallback a `extra_headers` (versiones viejas) cuando la
primera firma lanza `TypeError` — SIN abrir ningún socket real.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from norivy_kiosk.realtime.client import _default_connector

_URL = "wss://api.openai.com/v1/realtime?model=test"
_HEADERS = [("Authorization", "Bearer sk-test")]


def _install_fake_websockets(
    monkeypatch: pytest.MonkeyPatch, connect_fn: Any
) -> None:
    """Monta un módulo fake `websockets.asyncio.client` con el connect dado."""
    pkg = types.ModuleType("websockets")
    asyncio_mod = types.ModuleType("websockets.asyncio")
    client_mod = types.ModuleType("websockets.asyncio.client")
    client_mod.connect = connect_fn  # type: ignore[attr-defined]
    asyncio_mod.client = client_mod  # type: ignore[attr-defined]
    pkg.asyncio = asyncio_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "websockets", pkg)
    monkeypatch.setitem(sys.modules, "websockets.asyncio", asyncio_mod)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.client", client_mod)


class _FakeWS:
    """Marcador de "socket abierto" que devuelve el connect fake."""


@pytest.mark.integration
async def test_default_connector_usa_additional_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # websockets 13+: connect acepta `additional_headers` → se usa ese camino.
    seen: dict[str, Any] = {}

    async def fake_connect(url: str, **kwargs: Any) -> _FakeWS:
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _FakeWS()

    _install_fake_websockets(monkeypatch, fake_connect)

    ws = await _default_connector(
        _URL, headers=_HEADERS, ping_interval=20.0, ping_timeout=20.0
    )

    assert isinstance(ws, _FakeWS)
    assert seen["url"] == _URL
    assert seen["kwargs"]["additional_headers"] == _HEADERS
    assert seen["kwargs"]["ping_interval"] == 20.0
    assert seen["kwargs"]["ping_timeout"] == 20.0


@pytest.mark.integration
async def test_default_connector_fallback_a_extra_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # websockets viejo: `additional_headers` lanza TypeError → fallback a
    # `extra_headers`. El connector debe reintentar con la firma vieja y funcionar.
    calls: list[dict[str, Any]] = []

    async def fake_connect(url: str, **kwargs: Any) -> _FakeWS:
        calls.append(kwargs)
        if "additional_headers" in kwargs:
            raise TypeError("connect() got an unexpected keyword 'additional_headers'")
        return _FakeWS()

    _install_fake_websockets(monkeypatch, fake_connect)

    ws = await _default_connector(
        _URL, headers=_HEADERS, ping_interval=20.0, ping_timeout=20.0
    )

    assert isinstance(ws, _FakeWS)
    assert len(calls) == 2  # primer intento (nuevo) + fallback (viejo)
    assert "additional_headers" in calls[0]
    assert calls[1]["extra_headers"] == _HEADERS
