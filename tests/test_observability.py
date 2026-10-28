"""TDD B2 — el backend EXPONE su estado real para el operador.

Comentario en español: el patrón peligroso de la auditoría era "falla en
silencio con señal engañosa". Estos estados (WS a OpenAI vivo, mic vivo,
sesión activa) deben ser observables para que el renderer/operador vean qué
pasa en vez de un "Conectada" mentiroso.
"""

from __future__ import annotations

import asyncio

import pytest

from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime.audio import MicCapture
from norivy_kiosk.realtime.client import OpenAIRealtimeClient
from norivy_kiosk.realtime.session import RealtimeSession


@pytest.mark.unit
def test_client_connected_refleja_el_ws() -> None:
    client = OpenAIRealtimeClient()
    assert client.connected is False  # sin conectar

    client._ws = object()  # simula un WS abierto
    assert client.connected is True


@pytest.mark.unit
def test_mic_is_alive_refleja_el_stream() -> None:
    loop = asyncio.new_event_loop()
    try:
        mic = MicCapture(sample_rate=24000, channels=1, device=None, loop=loop)
        assert mic.is_alive is False  # sin start(), no hay stream
    finally:
        loop.close()


@pytest.mark.unit
def test_hello_incluye_realtime_active() -> None:
    session = RealtimeSession(Hub())
    hello = session.hello()
    assert hello["realtime_active"] is False  # la sesión no arrancó
