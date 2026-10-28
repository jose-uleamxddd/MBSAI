"""TDD FIX 3 — ``response.done`` debe leer ``status`` (fallo ≠ éxito).

Comentario en español: una respuesta que terminó en ``failed``/``cancelled``/
``incomplete`` NO puede verse igual que una exitosa. Además de sellar el estado
(``speech_done``), se publica la razón (de ``status_details``) para que el
operador/renderer sepan que algo salió mal. Sin red: se procesan mensajes
directamente por el receive loop con un WS falso.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from norivy_kiosk import config
from norivy_kiosk.realtime.client import OpenAIRealtimeClient


async def _drain_events(client: OpenAIRealtimeClient) -> list[dict]:
    """Vacía la cola de eventos publicados (sin bloquear)."""
    out: list[dict] = []
    while not client.events.empty():
        out.append(client.events.get_nowait())
    return out


class _ScriptedWS:
    """WS falso: entrega mensajes y luego bloquea (no cae)."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        pass

    def __aiter__(self) -> _ScriptedWS:
        return self

    async def __anext__(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(3600)
        raise StopAsyncIteration


async def _run_loop_with(messages: list[str]) -> list[dict]:
    """Corre _receive_loop con los mensajes dados y devuelve lo publicado."""
    client = OpenAIRealtimeClient()
    client._ws = _ScriptedWS(messages)
    loop_task = asyncio.create_task(client._receive_loop())
    for _ in range(50):
        await asyncio.sleep(0)
    events = await _drain_events(client)
    loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop_task
    return events


@pytest.mark.integration
async def test_response_done_failed_publica_razon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    msg = json.dumps(
        {
            "type": "response.done",
            "response": {
                "status": "failed",
                "status_details": {
                    "error": {"type": "server_error", "message": "boom"}
                },
            },
        }
    )

    events = await _run_loop_with([msg])
    kinds = [e.get("kind") for e in events]

    # Sella el estado Y publica el fallo: no es idéntico a un done exitoso.
    assert "speech_done" in kinds
    failures = [e for e in events if e.get("kind") == "response_failed"]
    assert len(failures) == 1
    assert failures[0]["status"] == "failed"
    assert "boom" in failures[0]["message"]


@pytest.mark.integration
async def test_response_done_exitoso_no_publica_fallo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    msg = json.dumps(
        {"type": "response.done", "response": {"status": "completed"}}
    )

    events = await _run_loop_with([msg])
    kinds = [e.get("kind") for e in events]

    assert "speech_done" in kinds
    assert "response_failed" not in kinds


@pytest.mark.integration
async def test_response_done_incompleto_publica_fallo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    msg = json.dumps(
        {
            "type": "response.done",
            "response": {
                "status": "incomplete",
                "status_details": {"reason": "max_output_tokens"},
            },
        }
    )

    events = await _run_loop_with([msg])
    failures = [e for e in events if e.get("kind") == "response_failed"]

    assert len(failures) == 1
    assert failures[0]["status"] == "incomplete"
    assert "max_output_tokens" in failures[0]["message"]


@pytest.mark.integration
async def test_response_done_cancelled_por_barge_in_no_es_fallo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # El público interrumpió (turn_detected) → OpenAI cancela la respuesta. Es el
    # barge-in NORMAL, no un fallo: no debe generar un banner de error en escena.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    msg = json.dumps(
        {
            "type": "response.done",
            "response": {
                "status": "cancelled",
                "status_details": {"type": "cancelled", "reason": "turn_detected"},
            },
        }
    )

    events = await _run_loop_with([msg])
    kinds = [e.get("kind") for e in events]

    assert "speech_done" in kinds
    assert "response_failed" not in kinds


@pytest.mark.integration
async def test_response_done_cancelled_por_otra_razon_si_publica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Un cancelled por una razón distinta al barge-in SÍ es digno de reportar.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    msg = json.dumps(
        {
            "type": "response.done",
            "response": {
                "status": "cancelled",
                "status_details": {"type": "cancelled", "reason": "client_cancelled"},
            },
        }
    )

    events = await _run_loop_with([msg])
    failures = [e for e in events if e.get("kind") == "response_failed"]

    assert len(failures) == 1
    assert failures[0]["status"] == "cancelled"
    assert "client_cancelled" in failures[0]["message"]
