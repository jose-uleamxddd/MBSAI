"""Caracterización del Hub — broadcast y descarte de clientes muertos.

Comentario en español: locka el comportamiento actual del fan-out antes de
refactorizarlo (F3 saca el send_text del lock y lo paraleliza con gather).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from norivy_kiosk.hub import Hub
from tests.conftest import FakeWebSocket


@pytest.mark.unit
async def test_broadcast_llega_a_todos_los_clientes() -> None:
    # Arrange
    hub = Hub()
    a, b = FakeWebSocket(), FakeWebSocket()
    await hub.register(a)
    await hub.register(b)

    # Act
    await hub.broadcast({"type": "ping"})

    # Assert
    assert json.loads(a.sent[0]) == {"type": "ping"}
    assert json.loads(b.sent[0]) == {"type": "ping"}


@pytest.mark.unit
async def test_broadcast_sin_clientes_no_explota() -> None:
    hub = Hub()
    await hub.broadcast({"type": "ping"})  # no debe lanzar


@pytest.mark.unit
async def test_broadcast_descarta_cliente_que_falla() -> None:
    # Arrange
    hub = Hub()
    ok, bad = FakeWebSocket(), FakeWebSocket(fail=True)
    await hub.register(ok)
    await hub.register(bad)

    # Act — el cliente que falla se descarta durante el broadcast
    await hub.broadcast({"type": "uno"})

    # Assert — el bueno recibió; el segundo broadcast ya no intenta el muerto
    assert len(ok.sent) == 1
    await hub.broadcast({"type": "dos"})
    assert len(ok.sent) == 2


@pytest.mark.unit
async def test_unregister_saca_al_cliente() -> None:
    hub = Hub()
    ws = FakeWebSocket()
    await hub.register(ws)
    await hub.unregister(ws)

    await hub.broadcast({"type": "ping"})

    assert ws.sent == []


@pytest.mark.integration
async def test_broadcast_envia_en_paralelo_no_en_serie() -> None:
    """Un cliente lento NO debe frenar el envío a los demás (sin head-of-line)."""
    # Arrange: dos clientes que bloquean hasta que AMBOS arrancaron su send.
    hub = Hub()
    started = 0
    both_started = asyncio.Event()
    proceed = asyncio.Event()

    class BlockingWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, text: str) -> None:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await proceed.wait()
            self.sent.append(text)

    a, b = BlockingWS(), BlockingWS()
    await hub.register(a)
    await hub.register(b)

    # Act
    task = asyncio.create_task(hub.broadcast({"type": "x"}))

    # Assert: en serie, el 2º send nunca arranca (el 1º bloquea el lock) → timeout.
    # En paralelo, ambos arrancan y both_started se dispara.
    await asyncio.wait_for(both_started.wait(), timeout=1.0)

    proceed.set()
    await task
    assert a.sent and b.sent
