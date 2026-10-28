"""Function calling: eventos del cliente Realtime y ejecución en la sesión.

Comentario en español: el cliente traduce ``function_call_arguments.done`` a un
evento ``function_call``; la sesión ejecuta la tool con el ``ToolRunner`` y devuelve
el resultado con ``send_function_result`` (item + response.create). ``end_session``
borra carrito y conversación. Sin red: WS y tienda son dobles.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime.client import OpenAIRealtimeClient
from norivy_kiosk.realtime.session import RealtimeSession
from norivy_kiosk.tools import TOOLS


class _WS:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True


def _types(ws: _WS) -> list[str]:
    return [f["type"] for f in ws.sent]


async def test_session_update_inicial_declara_las_tools() -> None:
    client = OpenAIRealtimeClient()
    ws = _WS()
    client._ws = ws

    await client._send_session_update(create_response=False)

    session = ws.sent[0]["session"]
    assert session["tools"] == TOOLS
    assert session["tool_choice"] == "auto"


def test_function_call_done_se_normaliza_con_el_nombre_del_item() -> None:
    client = OpenAIRealtimeClient()
    client._normalize(
        {
            "type": "response.output_item.added",
            "item": {
                "id": "i1",
                "type": "function_call",
                "call_id": "c1",
                "name": "buscar_productos",
            },
        }
    )

    event = client._normalize(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "c1",
            "arguments": '{"texto":"filtro"}',
        }
    )

    assert event == {
        "kind": "function_call",
        "call_id": "c1",
        "name": "buscar_productos",
        "arguments": '{"texto":"filtro"}',
    }


def test_function_call_done_sin_call_id_se_ignora() -> None:
    client = OpenAIRealtimeClient()
    assert client._normalize({"type": "response.function_call_arguments.done"}) is None


async def test_send_function_result_manda_item_y_luego_response_create() -> None:
    client = OpenAIRealtimeClient()
    ws = _WS()
    client._ws = ws

    await client.send_function_result("c1", '{"ok":true}')

    assert _types(ws) == ["conversation.item.create", "response.create"]
    item = ws.sent[0]["item"]
    assert item == {
        "type": "function_call_output",
        "call_id": "c1",
        "output": '{"ok":true}',
    }


async def test_send_function_result_sin_ws_no_hace_nada() -> None:
    client = OpenAIRealtimeClient()
    await client.send_function_result("c1", "{}")  # no debe lanzar


async def test_restart_cierra_el_ws_y_sin_ws_es_noop() -> None:
    client = OpenAIRealtimeClient()
    await client.restart()  # sin WS: no-op
    ws = _WS()
    client._ws = ws

    await client.restart()

    assert ws.closed is True


class _FakeClient:
    """Cliente Realtime espía para la sesión."""

    def __init__(self) -> None:
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.connected = True
        self.is_speaking = False
        self.results: list[tuple[str, str]] = []
        self.restarts = 0
        self.listening: list[bool] = []

    async def send_function_result(self, call_id: str, output: str) -> None:
        self.results.append((call_id, output))

    async def restart(self) -> None:
        self.restarts += 1

    async def set_mic_listening(self, listening: bool) -> None:
        self.listening.append(listening)

    async def commit_and_respond(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.resets = 0

    async def run(self, name: str, arguments: str) -> str:
        self.calls.append((name, arguments))
        return '{"ok":true}'

    def reset(self) -> None:
        self.resets += 1

    async def aclose(self) -> None:
        pass


class _RecordingHub(Hub):
    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, Any]] = []

    async def broadcast(self, message: dict[str, Any]) -> None:
        self.frames.append(message)


def _session() -> tuple[RealtimeSession, _FakeClient, _FakeRunner, _RecordingHub]:
    hub, client, runner = _RecordingHub(), _FakeClient(), _FakeRunner()
    session = RealtimeSession(hub, client=client, runner=runner)  # type: ignore[arg-type]
    return session, client, runner, hub


async def test_function_call_ejecuta_la_tool_y_devuelve_el_resultado() -> None:
    session, client, runner, hub = _session()

    await session._broadcast_event(
        {
            "kind": "function_call",
            "call_id": "c1",
            "name": "buscar_productos",
            "arguments": "{}",
        }
    )
    await asyncio.gather(*list(session._tool_tasks))

    assert runner.calls == [("buscar_productos", "{}")]
    assert client.results == [("c1", '{"ok":true}')]
    assert {
        "type": "status",
        "state": "working",
        "detail": "buscar_productos",
    } in hub.frames


@pytest.mark.parametrize("muted", [True, False])
async def test_end_session_limpia_carrito_conversacion_y_avisa(muted: bool) -> None:
    session, client, runner, hub = _session()
    await session.set_mic_muted(muted)

    await session.end_session()

    assert session.mic_muted is True
    assert runner.resets == 1
    assert client.restarts == 1
    assert client.listening[-1] is False
    assert {"type": "session_reset"} in hub.frames
    assert {"type": "mic_state", "muted": True} in hub.frames
