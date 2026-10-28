"""TDD — bridge en vivo: `_broadcast_event` traduce eventos del cliente a WS.

Comentario en español: este path (evento normalizado del cliente → frame del
protocolo WS que consume el renderer) estaba a 0% de cobertura. Es el contrato
que ROMPE la conferencia si diverge: cada `kind` del cliente debe mapear al frame
exacto que el renderer espera. La red y el cliente se INYECTAN (fake) — nunca
tocamos OpenAI real.
"""

from __future__ import annotations

from typing import Any

import pytest

from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime.session import RealtimeSession


class RecordingHub(Hub):
    """Hub que captura los frames broadcasteados en vez de mandarlos por red."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, Any]] = []

    async def broadcast(self, message: dict[str, Any]) -> None:
        self.frames.append(message)


def _session_with_recording_hub() -> tuple[RealtimeSession, RecordingHub]:
    hub = RecordingHub()
    session = RealtimeSession(hub)
    return session, hub


def _types(frames: list[dict[str, Any]]) -> list[str]:
    return [f.get("type") for f in frames]


@pytest.mark.unit
async def test_broadcast_audio_no_incluye_seq_y_lleva_pcm16() -> None:
    # Arrange: el cliente Realtime YA NO emite `seq` (se quitó por dead-code); el
    # frame `audio` broadcasteado debe llevar solo el PCM16, sin `seq` (KeyError).
    session, hub = _session_with_recording_hub()

    # Act
    await session._broadcast_event({"kind": "audio", "pcm16_b64": "QUJD"})

    # Assert: exactamente un frame audio, con pcm16 y SIN seq.
    assert len(hub.frames) == 1
    frame = hub.frames[0]
    assert frame["type"] == "audio"
    assert frame["pcm16"] == "QUJD"
    assert "seq" not in frame


@pytest.mark.unit
async def test_broadcast_transcript() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "transcript", "delta": "hola"})
    assert hub.frames == [{"type": "transcript", "delta": "hola"}]


@pytest.mark.unit
async def test_broadcast_speech_started_abre_burbuja_y_status_speaking() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "speech_started"})
    assert _types(hub.frames) == ["speech_started", "status"]
    assert hub.frames[1] == {"type": "status", "state": "speaking", "detail": ""}


@pytest.mark.unit
async def test_broadcast_speech_done_cierra_y_status_connected() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "speech_done"})
    assert _types(hub.frames) == ["speech_done", "status"]
    assert hub.frames[1] == {"type": "status", "state": "connected", "detail": ""}


@pytest.mark.unit
async def test_broadcast_user_speech_started_y_status_listening() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "user_speech_started"})
    assert _types(hub.frames) == ["user_speech_started", "status"]
    assert hub.frames[1] == {"type": "status", "state": "listening", "detail": ""}


@pytest.mark.unit
async def test_broadcast_user_transcript() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "user_transcript", "delta": "che"})
    assert hub.frames == [{"type": "user_transcript", "delta": "che"}]


@pytest.mark.unit
async def test_broadcast_user_transcript_done() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event(
        {"kind": "user_transcript_done", "transcript": "una pregunta"}
    )
    assert hub.frames == [
        {"type": "user_transcript_done", "transcript": "una pregunta"}
    ]


@pytest.mark.unit
async def test_broadcast_status_pasa_state_y_detail() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event(
        {"kind": "status", "state": "reconnecting", "detail": "intento 2"}
    )
    assert hub.frames == [
        {"type": "status", "state": "reconnecting", "detail": "intento 2"}
    ]


@pytest.mark.unit
async def test_broadcast_error_emite_error_y_status_error() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "error", "message": "algo falló"})
    assert _types(hub.frames) == ["error", "status"]
    assert hub.frames[0] == {"type": "error", "message": "algo falló"}
    assert hub.frames[1] == {"type": "status", "state": "error", "detail": "algo falló"}


@pytest.mark.unit
async def test_broadcast_fatal_es_sticky_con_state_fatal() -> None:
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "fatal", "message": "cuota agotada"})
    assert _types(hub.frames) == ["error", "status"]
    assert hub.frames[1] == {
        "type": "status",
        "state": "fatal",
        "detail": "cuota agotada",
    }


@pytest.mark.unit
async def test_broadcast_rate_warning_avisa_al_renderer() -> None:
    # rate_warning (NUEVO del cliente): cuota cayendo. El renderer no conoce ese
    # tipo (default: break) → debe rutearse a un frame que SÍ pinta (error info).
    session, hub = _session_with_recording_hub()
    await session._broadcast_event(
        {"kind": "rate_warning", "name": "tokens", "remaining": 100, "limit": 1000}
    )
    # Se emite algo que el renderer entiende (error/status), no un tipo huérfano.
    assert hub.frames, "rate_warning no debe descartarse en silencio"
    assert all(f["type"] in ("error", "status") for f in hub.frames)


@pytest.mark.unit
async def test_broadcast_response_failed_avisa_al_renderer() -> None:
    # response_failed (NUEVO del cliente): un turno de Norivy falló/cancelado.
    session, hub = _session_with_recording_hub()
    await session._broadcast_event(
        {"kind": "response_failed", "status": "failed", "message": "server error"}
    )
    assert hub.frames, "response_failed no debe descartarse en silencio"
    assert all(f["type"] in ("error", "status") for f in hub.frames)
    # El mensaje de la falla debe viajar (observabilidad para el operador).
    assert any("server error" in str(f.get("message", "")) for f in hub.frames)


@pytest.mark.unit
async def test_broadcast_kind_desconocido_no_crashea_ni_emite() -> None:
    # Un kind no mapeado no debe romper el bridge ni broadcastear basura.
    session, hub = _session_with_recording_hub()
    await session._broadcast_event({"kind": "algo_nuevo_no_mapeado"})
    assert hub.frames == []
