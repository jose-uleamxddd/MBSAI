"""TDD FIX — la sesión commitea al cerrar el mic (tecla M → mutear).

Comentario en español: ``set_mic_muted(True)`` debe pedirle al cliente que cierre
el turno del usuario a mano (``commit_and_respond``) para que Norivy responda con
lo que alcanzó a escuchar, sin esperar a que se reabra el mic. Abrir el mic
(``set_mic_muted(False)``) NO debe commitear. El cliente se INYECTA (fake que
registra la llamada) — nunca tocamos OpenAI real.
"""

from __future__ import annotations

from typing import Any

import pytest

from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime.session import RealtimeSession


class _CommitSpyClient:
    """Cliente fake: registra listening + cuántas veces se pidió el commit."""

    def __init__(self) -> None:
        self.listening_calls: list[bool] = []
        self.commit_calls = 0

    @property
    def is_speaking(self) -> bool:
        return False

    async def set_mic_listening(self, listening: bool) -> None:
        self.listening_calls.append(listening)

    async def commit_and_respond(self) -> None:
        self.commit_calls += 1


class _RecordingHub(Hub):
    """Hub que captura los frames broadcasteados en vez de mandarlos por red."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, Any]] = []

    async def broadcast(self, message: dict[str, Any]) -> None:
        self.frames.append(message)


@pytest.mark.unit
async def test_mutear_el_mic_commitea_y_dispara_respuesta() -> None:
    # Arrange
    client = _CommitSpyClient()
    session = RealtimeSession(_RecordingHub(), client=client)

    # Act: el operador cierra el mic a mitad de frase.
    await session.set_mic_muted(True)

    # Assert: cerrar el mic = "terminé, respondé" → un solo commit.
    assert client.commit_calls == 1


@pytest.mark.unit
async def test_abrir_el_mic_no_commitea() -> None:
    # Arrange
    client = _CommitSpyClient()
    session = RealtimeSession(_RecordingHub(), client=client)

    # Act: el operador ABRE el mic (empieza a escuchar).
    await session.set_mic_muted(False)

    # Assert: abrir el mic NO cierra ningún turno.
    assert client.commit_calls == 0
