"""TDD F3 — la cola de eventos del cliente cuenta descartes (no silenciosos).

Comentario en español: cuando la cola se satura, el descarte del evento más
viejo debe quedar CONTABILIZADO (y logueado) para que un pipeline que no da
abasto sea visible, en vez de perder audio/control en silencio.
"""

from __future__ import annotations

import pytest

from norivy_kiosk import config
from norivy_kiosk.realtime.client import OpenAIRealtimeClient


@pytest.mark.unit
async def test_cola_de_eventos_saturada_cuenta_descartes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: cola chica para forzar saturación sin encolar cientos.
    monkeypatch.setattr(config, "EVENTS_QUEUE_MAX", 3)
    client = OpenAIRealtimeClient()

    # Act: publicar más eventos que la capacidad de la cola
    for i in range(6):
        await client._publish({"kind": "audio", "pcm16_b64": f"chunk{i}"})

    # Assert: los sobrantes se descartaron y quedaron contabilizados
    assert client.dropped_events == 3  # 6 publicados, cap 3 → 3 descartados
    assert client.events.qsize() == 3


@pytest.mark.unit
async def test_cola_sin_saturar_no_descarta_nada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "EVENTS_QUEUE_MAX", 10)
    client = OpenAIRealtimeClient()

    for i in range(4):
        await client._publish({"kind": "audio", "pcm16_b64": f"chunk{i}"})

    assert client.dropped_events == 0
    assert client.events.qsize() == 4
