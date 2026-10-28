"""TDD FIX 7 — degradar limpio si el WS muere a mitad de un ``send``.

Comentario en español: si el WS de OpenAI cae DURANTE un ``await ws.send`` (no
antes, cuando ``_ws is None``, sino en pleno envío), los métodos de acción
(``create_greeting``, ``set_mic_listening``, ``append_audio``) deben degradar en
silencio-limpio (log DEBUG, sin propagar): el supervisor reconecta. Antes subía
una excepción de socket → warning ruidoso + acción perdida. Sin red real.
"""

from __future__ import annotations

import pytest

from norivy_kiosk.realtime.client import OpenAIRealtimeClient


class _DeadSendWS:
    """WS 'abierto' cuyo send falla como si el socket muriera a mitad del envío."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        raise self._exc


@pytest.mark.unit
async def test_create_greeting_no_propaga_si_el_ws_muere_en_send() -> None:
    client = OpenAIRealtimeClient()
    client._ws = _DeadSendWS(ConnectionError("socket cerrado a mitad"))

    # Act + Assert: NO debe propagar; degrada limpio (el supervisor reconecta).
    await client.create_greeting()


@pytest.mark.unit
async def test_set_mic_listening_no_propaga_si_el_ws_muere_en_send() -> None:
    client = OpenAIRealtimeClient()
    client._ws = _DeadSendWS(ConnectionError("socket cerrado a mitad"))

    await client.set_mic_listening(True)  # no raise


@pytest.mark.unit
async def test_append_audio_no_propaga_si_el_ws_muere_en_send() -> None:
    client = OpenAIRealtimeClient()
    client._ws = _DeadSendWS(RuntimeError("send tras close"))

    await client.append_audio(b"\x00\x01\x02\x03")  # no raise


@pytest.mark.unit
async def test_send_json_traga_runtime_error_de_socket_muerto() -> None:
    # RuntimeError es lo que websockets suele levantar al mandar tras el cierre.
    client = OpenAIRealtimeClient()
    client._ws = _DeadSendWS(RuntimeError("cannot send on closing connection"))

    await client.create_greeting()  # no raise
