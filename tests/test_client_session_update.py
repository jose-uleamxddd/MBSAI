"""TDD — el turn_detection del session.update usa ``server_vad`` con threshold.

Comentario en español: el default es ``server_vad`` (rechaza audio de fondo —
TikTok, videos, bulla — con un ``threshold`` de sensibilidad, ideal para el mic
de auditorio cercano a la boca). ``semantic_vad`` (el anterior, que detecta
CUALQUIER voz por significado) queda como opción vía ``VAD_MODE``. Además el
audio de entrada pasa por ``noise_reduction`` (``far_field`` por defecto) antes
del detector. Sin red: capturamos el JSON que el cliente manda por un WS falso.
"""

from __future__ import annotations

import json

import pytest

from norivy_kiosk import config
from norivy_kiosk.realtime.client import OpenAIRealtimeClient


class _CapturingWS:
    """WS mínimo que captura lo enviado."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


def _last_session_update(sent: list[str]) -> dict:
    """Devuelve el último ``session.update`` enviado (dict completo)."""
    for raw in reversed(sent):
        payload = json.loads(raw)
        if payload.get("type") == "session.update":
            return payload
    raise AssertionError("no se envió ningún session.update")


def _turn_detection_of_last_session_update(sent: list[str]) -> dict:
    """Extrae el turn_detection del último session.update enviado."""
    payload = _last_session_update(sent)
    return payload["session"]["audio"]["input"]["turn_detection"]


@pytest.mark.unit
async def test_session_update_inicial_usa_server_vad_con_threshold() -> None:
    # Arrange: sin monkeypatch → defaults reales (server_vad, threshold 0.6).
    client = OpenAIRealtimeClient()
    client._ws = _CapturingWS()

    # Act
    await client._send_session_update(create_response=True)

    # Assert: server_vad con umbral y timings del default, half-duplex intacto.
    td = _turn_detection_of_last_session_update(client._ws.sent)
    assert td["type"] == "server_vad"
    assert td["threshold"] == pytest.approx(0.6)
    assert td["prefix_padding_ms"] == 300
    assert td["silence_duration_ms"] == 700
    assert td["create_response"] is True
    # Half-duplex: una vez que Norivy arranca, NADA la interrumpe (turnos limpios).
    assert td["interrupt_response"] is False


@pytest.mark.unit
async def test_server_vad_respeta_create_response_false() -> None:
    # Arrange: mic muteado → el auto-create_response se apaga.
    client = OpenAIRealtimeClient()
    client._ws = _CapturingWS()

    # Act
    await client._send_session_update(create_response=False)

    # Assert
    td = _turn_detection_of_last_session_update(client._ws.sent)
    assert td["type"] == "server_vad"
    assert td["create_response"] is False


@pytest.mark.unit
async def test_vad_mode_semantic_conserva_comportamiento_viejo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: modo semantic_vad explícito (comportamiento anterior intacto).
    monkeypatch.setattr(config, "VAD_MODE", "semantic_vad")
    monkeypatch.setattr(config, "VAD_EAGERNESS", "low")
    client = OpenAIRealtimeClient()
    client._ws = _CapturingWS()

    # Act
    await client._send_session_update(create_response=True)

    # Assert: vuelve al semantic_vad con eagerness, sin threshold.
    td = _turn_detection_of_last_session_update(client._ws.sent)
    assert td["type"] == "semantic_vad"
    assert td["eagerness"] == "low"
    assert td["interrupt_response"] is False
    assert "threshold" not in td


@pytest.mark.unit
async def test_set_mic_listening_respeta_vad_mode_semantic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: el update de mic también arrastra el modo/eagerness configurado.
    monkeypatch.setattr(config, "VAD_MODE", "semantic_vad")
    monkeypatch.setattr(config, "VAD_EAGERNESS", "high")
    client = OpenAIRealtimeClient()
    client._ws = _CapturingWS()

    # Act
    await client.set_mic_listening(True)

    # Assert
    td = _turn_detection_of_last_session_update(client._ws.sent)
    assert td["type"] == "semantic_vad"
    assert td["eagerness"] == "high"


@pytest.mark.unit
async def test_set_mic_listening_usa_server_vad_por_defecto() -> None:
    # Arrange: sin monkeypatch → el update de mic usa el server_vad default.
    client = OpenAIRealtimeClient()
    client._ws = _CapturingWS()

    # Act
    await client.set_mic_listening(True)

    # Assert
    td = _turn_detection_of_last_session_update(client._ws.sent)
    assert td["type"] == "server_vad"
    assert td["threshold"] == pytest.approx(0.6)
    assert td["create_response"] is True


@pytest.mark.unit
async def test_session_update_incluye_noise_reduction_far_field() -> None:
    # Arrange: default NOISE_REDUCTION=far_field (mic de la pantalla, lejano).
    client = OpenAIRealtimeClient()
    client._ws = _CapturingWS()

    # Act
    await client._send_session_update(create_response=True)

    # Assert: el noise_reduction va anidado en audio.input.
    payload = _last_session_update(client._ws.sent)
    audio_input = payload["session"]["audio"]["input"]
    assert audio_input["noise_reduction"] == {"type": "far_field"}


@pytest.mark.unit
async def test_session_update_omite_noise_reduction_cuando_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: NOISE_REDUCTION=off → la clave NO debe incluirse (OpenAI lo apaga
    # OMITIENDO el campo entero, no mandando un valor).
    monkeypatch.setattr(config, "NOISE_REDUCTION", "off")
    client = OpenAIRealtimeClient()
    client._ws = _CapturingWS()

    # Act
    await client._send_session_update(create_response=True)

    # Assert
    payload = _last_session_update(client._ws.sent)
    audio_input = payload["session"]["audio"]["input"]
    assert "noise_reduction" not in audio_input
