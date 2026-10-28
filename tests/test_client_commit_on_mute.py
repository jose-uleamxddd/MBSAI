"""TDD FIX — commit al cerrar el mic: cerrar el mic a mitad de frase = "terminé,
respondé con lo que escuchaste" (como soltar un push-to-talk).

Comentario en español: ``set_mic_muted(True)`` solo apagaba el ``create_response``
del ``semantic_vad``, dejando el audio ya capturado en el buffer de OpenAI sin
dispararse hasta reabrir el mic. ``commit_and_respond`` cierra el turno a mano:
commitea el input buffer + dispara ``response.create``. Guardas: no-op sin habla
del usuario (evita ``input_audio_buffer_commit_empty``) y no-op si Norivy YA está
respondiendo (no disparar una segunda respuesta encima). La red se INYECTA: estos
tests NUNCA tocan OpenAI real.
"""

from __future__ import annotations

import json

import pytest

from norivy_kiosk.realtime.client import OpenAIRealtimeClient


def _sent_of_type(ws_sent: list[str], msg_type: str) -> list[dict]:
    """Filtra los frames JSON enviados por el cliente de un ``type`` dado."""
    out: list[dict] = []
    for raw in ws_sent:
        payload = json.loads(raw)
        if payload.get("type") == msg_type:
            out.append(payload)
    return out


class _CapturingWS:
    """WS mínimo que solo captura lo enviado (no entrega mensajes)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.unit
async def test_commit_and_respond_commitea_y_dispara_respuesta() -> None:
    # Arrange: WS abierto con habla del usuario en curso (speech_started activa
    # _user_turn_active), sin respuesta de Norivy todavía.
    client = OpenAIRealtimeClient()
    ws = _CapturingWS()
    client._ws = ws
    client._normalize({"type": "input_audio_buffer.speech_started"})

    # Act: el operador cierra el mic a mitad de frase.
    await client.commit_and_respond()

    # Assert: se cerró el turno a mano (commit) + se disparó la respuesta.
    assert len(_sent_of_type(ws.sent, "input_audio_buffer.commit")) == 1
    assert len(_sent_of_type(ws.sent, "response.create")) == 1


@pytest.mark.unit
async def test_commit_and_respond_sin_habla_no_envia_nada() -> None:
    # Arrange: WS abierto pero el usuario nunca habló (_user_turn_active False):
    # commitear un buffer vacío daría input_audio_buffer_commit_empty.
    client = OpenAIRealtimeClient()
    ws = _CapturingWS()
    client._ws = ws

    # Act
    await client.commit_and_respond()

    # Assert: nada enviado (ni commit ni response.create).
    assert ws.sent == []


@pytest.mark.unit
async def test_commit_and_respond_con_norivy_respondiendo_no_envia_nada() -> None:
    # Arrange: aislamos la guarda de _response_active. Seteamos AMBOS flags a mano
    # (response.created resetearía _user_turn_active a False, tapando esta guarda):
    # hay habla del usuario pendiente PERO Norivy ya está respondiendo.
    client = OpenAIRealtimeClient()
    ws = _CapturingWS()
    client._ws = ws
    client._user_turn_active = True
    client._response_active = True

    # Act
    await client.commit_and_respond()

    # Assert: no disparar una segunda respuesta encima de la de Norivy.
    assert ws.sent == []


@pytest.mark.unit
async def test_commit_and_respond_sin_ws_no_crashea() -> None:
    # Arrange: hay habla pendiente pero el WS cayó (self._ws is None).
    client = OpenAIRealtimeClient()
    client._user_turn_active = True

    # Act + Assert: no debe propagar aunque el socket esté muerto.
    await client.commit_and_respond()  # no raise


@pytest.mark.unit
async def test_response_created_cierra_el_turno_del_usuario() -> None:
    # Arrange: había habla del usuario pendiente.
    client = OpenAIRealtimeClient()
    client._normalize({"type": "input_audio_buffer.speech_started"})
    assert client._user_turn_active is True

    # Act: Norivy empieza a responder.
    client._normalize({"type": "response.created"})

    # Assert: el turno del usuario ya se está respondiendo → flag en False.
    assert client._user_turn_active is False
