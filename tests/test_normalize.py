"""Caracterización de OpenAIRealtimeClient._normalize — mapeo protocolo GA.

Comentario en español: `_normalize` traduce cada evento del servidor Realtime
GA al evento interno del backend. Es lógica pura y es EXACTAMENTE lo que se va a
refactorizar a dispatch table (F4). Estos tests lockan el mapeo actual para que
el refactor no cambie comportamiento observable.
"""

from __future__ import annotations

import pytest

from norivy_kiosk.realtime.client import OpenAIRealtimeClient


@pytest.mark.unit
def test_response_created_abre_turno_y_marca_speech_started() -> None:
    client = OpenAIRealtimeClient()
    assert client._normalize({"type": "response.created"}) == {
        "kind": "speech_started"
    }


@pytest.mark.unit
def test_audio_delta_lleva_el_pcm16_sin_seq() -> None:
    # El campo `seq` era dead-code de protocolo (el renderer nunca lo lee: WS
    # local loopback TCP, no reordena). El evento audio lleva solo el pcm16 b64.
    client = OpenAIRealtimeClient()

    event = client._normalize(
        {"type": "response.output_audio.delta", "delta": "QUJD"}
    )

    assert event == {"kind": "audio", "pcm16_b64": "QUJD"}


@pytest.mark.unit
def test_audio_delta_vacio_se_descarta() -> None:
    client = OpenAIRealtimeClient()
    assert (
        client._normalize({"type": "response.output_audio.delta", "delta": ""})
        is None
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "msg_type",
    ["response.done", "response.cancelled"],
)
def test_fin_de_respuesta_es_speech_done(msg_type: str) -> None:
    # response.failed NO es evento GA de primer nivel: el fallo viene dentro de
    # response.done.status (ver test_client_response_done.py). No se mapea acá.
    client = OpenAIRealtimeClient()
    assert client._normalize({"type": msg_type}) == {"kind": "speech_done"}


@pytest.mark.unit
def test_transcript_delta_de_norivy() -> None:
    client = OpenAIRealtimeClient()
    event = client._normalize(
        {"type": "response.output_audio_transcript.delta", "delta": "hola"}
    )
    assert event == {"kind": "transcript", "delta": "hola"}


@pytest.mark.unit
def test_transcripcion_del_publico_delta_y_done() -> None:
    client = OpenAIRealtimeClient()

    delta = client._normalize(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "delta": "pre",
        }
    )
    done = client._normalize(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "pregunta",
        }
    )

    assert delta == {"kind": "user_transcript", "delta": "pre"}
    assert done == {"kind": "user_transcript_done", "transcript": "pregunta"}


@pytest.mark.unit
def test_error_extrae_mensaje() -> None:
    client = OpenAIRealtimeClient()
    assert client._normalize(
        {"type": "error", "error": {"message": "boom"}}
    ) == {"kind": "error", "message": "boom"}


@pytest.mark.unit
def test_evento_desconocido_se_ignora() -> None:
    client = OpenAIRealtimeClient()
    assert client._normalize({"type": "cosa.que.no.existe"}) is None
