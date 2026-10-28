"""TDD FIX 8 — begin/end degradan LIMPIO con el cliente actual (sin red).

Comentario en español: el cliente ya atrapa el fallo de socket muerto adentro
(`_send_json_safe` traga ConnectionError/OSError/RuntimeError y NO propaga). Por
eso `session.begin_session()`/`session.end_session()` NO deben propagar aunque el
WS de OpenAI muera a mitad de un envío — envolverlos en try/except sería dead
defensive code. Este test PRUEBA esa degradación con el cliente REAL + un WS
inyectado que falla (nunca tocamos OpenAI real).
"""

from __future__ import annotations

from typing import Any

import pytest

from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime.client import OpenAIRealtimeClient
from norivy_kiosk.realtime.session import RealtimeSession


class _DeadSendWS:
    """WS 'abierto' cuyo `send` falla como si el socket muriera en pleno envío."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        raise ConnectionError("socket cerrado a mitad del envío")


@pytest.mark.integration
async def test_begin_session_no_propaga_con_ws_muerto() -> None:
    # Arrange: cliente REAL con un WS que falla en send; response activa (fuerza el
    # camino response.cancel + response.create, ambos via _send_json_safe).
    client = OpenAIRealtimeClient()
    client._ws = _DeadSendWS()  # type: ignore[assignment]
    client._response_active = True
    session = RealtimeSession(Hub(), client=client)

    # Act + Assert: begin_session NO debe propagar (el cliente degrada solo).
    await session.begin_session()


@pytest.mark.integration
async def test_end_session_no_propaga_con_ws_muerto() -> None:
    # Arrange: cliente REAL con WS que falla; set_mic_listening usa _send_json_safe.
    client = OpenAIRealtimeClient()
    client._ws = _DeadSendWS()  # type: ignore[assignment]
    session = RealtimeSession(Hub(), client=client)
    await session.set_mic_muted(False)

    # Act + Assert: end_session NO debe propagar y deja el mic muteado.
    await session.end_session()
    assert session.mic_muted is True


@pytest.mark.integration
async def test_end_session_broadcastea_mic_state_aunque_el_ws_muera() -> None:
    # La degradación limpia NO debe tragarse el frame mic_state al renderer: el
    # badge del mic debe reflejar el cambio aunque OpenAI esté caído.
    frames: list[dict[str, Any]] = []

    class RecordingHub(Hub):
        async def broadcast(self, message: dict[str, Any]) -> None:
            frames.append(message)

    client = OpenAIRealtimeClient()
    client._ws = _DeadSendWS()  # type: ignore[assignment]
    session = RealtimeSession(RecordingHub(), client=client)

    await session.end_session()

    assert {"type": "mic_state", "muted": True} in frames
    assert {"type": "session_reset"} in frames
