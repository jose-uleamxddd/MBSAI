"""TDD F2 — resiliencia de red del cliente Realtime (reconexión + keepalive).

Comentario en español: simula la caída del WS de OpenAI inyectando un connector
falso. El cliente real NUNCA se conecta a la red en estos tests — el connector
inyectado devuelve WebSockets falsos que podemos hacer "caer" a voluntad.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest

from norivy_kiosk import config
from norivy_kiosk.realtime.client import OpenAIRealtimeClient


class FakeRealtimeWS:
    """WS falso: entrega mensajes programados y luego 'cae' (async-for termina)."""

    def __init__(self, messages: list[str] | None = None) -> None:
        self._messages = list(messages or [])
        self.sent: list[str] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> FakeRealtimeWS:
        return self

    async def __anext__(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration


class RecordingConnector:
    """Connector inyectable: registra cada intento y devuelve el WS del factory."""

    def __init__(self, ws_for_call: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._ws_for_call = ws_for_call

    async def __call__(
        self,
        url: str,
        *,
        headers: Any,
        ping_interval: Any,
        ping_timeout: Any,
    ) -> Any:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "ping_interval": ping_interval,
                "ping_timeout": ping_timeout,
            }
        )
        return self._ws_for_call(len(self.calls))


@pytest.mark.integration
async def test_reconecta_cuando_el_ws_de_openai_se_cae(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: cada WS cae de inmediato (sin mensajes). Avisamos en la 2da conexión.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    reconnected = asyncio.Event()

    def ws_for_call(n: int) -> FakeRealtimeWS:
        if n >= 2:
            reconnected.set()
        return FakeRealtimeWS(messages=[])

    connector = RecordingConnector(ws_for_call)
    client = OpenAIRealtimeClient(
        connector=connector, reconnect_min_s=0.01, reconnect_max_s=0.02
    )

    # Act
    await client.connect()
    await asyncio.wait_for(reconnected.wait(), timeout=2.0)

    # Assert: hubo al menos 2 intentos de conexión (inicial + reconexión)
    assert len(connector.calls) >= 2

    await client.close()


@pytest.mark.integration
async def test_emite_status_reconnecting_al_reconectar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: el WS siempre cae → el supervisor debe avisar "reconnecting".
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    seen_reconnecting = asyncio.Event()

    connector = RecordingConnector(lambda _n: FakeRealtimeWS(messages=[]))
    client = OpenAIRealtimeClient(
        connector=connector, reconnect_min_s=0.01, reconnect_max_s=0.02
    )

    async def watch_events() -> None:
        while True:
            event = await client.events.get()
            if event.get("kind") == "status" and event.get("state") == "reconnecting":
                seen_reconnecting.set()
                return

    watcher = asyncio.create_task(watch_events())

    # Act
    await client.connect()

    # Assert: el renderer recibe un status "reconnecting" durante la reconexión
    await asyncio.wait_for(seen_reconnecting.wait(), timeout=2.0)

    await client.close()
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher


@pytest.mark.integration
async def test_error_fatal_de_openai_no_reintenta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: OpenAI manda un error FATAL (cuota agotada / key inválida) por el
    # WS. Reintentar con la misma key/cuota es inútil: debe emitir un evento
    # "fatal" (que el renderer hará sticky) y NO reconectar en loop.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    fatal = json.dumps(
        {
            "type": "error",
            "error": {"code": "insufficient_quota", "message": "no quota"},
        }
    )
    connector = RecordingConnector(lambda _n: FakeRealtimeWS(messages=[fatal]))
    client = OpenAIRealtimeClient(
        connector=connector, reconnect_min_s=0.01, reconnect_max_s=0.02
    )

    seen_fatal = asyncio.Event()

    async def watch() -> None:
        while True:
            event = await client.events.get()
            if event.get("kind") == "fatal":
                seen_fatal.set()
                return

    watcher = asyncio.create_task(watch())

    # Act
    await client.connect()
    await asyncio.wait_for(seen_fatal.wait(), timeout=2.0)

    # Assert: NO reintentó — solo la conexión inicial, sin reconexiones
    await asyncio.sleep(0.1)
    assert len(connector.calls) == 1

    await client.close()
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher


class BlockingWS:
    """WS que se mantiene abierto: __anext__ bloquea (simula conexión viva)."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> BlockingWS:
        return self

    async def __anext__(self) -> str:
        await asyncio.sleep(3600)  # nunca entrega ni cierra hasta cancelación
        raise StopAsyncIteration


@pytest.mark.integration
async def test_no_reconecta_tras_close_deliberado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: WS vivo que no cae; close() debe cerrar SIN disparar reconexión.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    connector = RecordingConnector(lambda _n: BlockingWS())
    client = OpenAIRealtimeClient(
        connector=connector, reconnect_min_s=0.01, reconnect_max_s=0.02
    )

    # Act
    await client.connect()
    assert len(connector.calls) == 1  # solo la conexión inicial
    await client.close()
    await asyncio.sleep(0.05)  # dar chance a una (indebida) reconexión

    # Assert: cerrar deliberadamente NO reconecta
    assert len(connector.calls) == 1


@pytest.mark.integration
async def test_pasa_keepalive_al_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    connector = RecordingConnector(lambda _n: BlockingWS())
    client = OpenAIRealtimeClient(connector=connector)  # defaults de config

    # Act
    await client.connect()

    # Assert: el connector recibe los parámetros de keepalive de config
    assert connector.calls[0]["ping_interval"] == config.PING_INTERVAL_S
    assert connector.calls[0]["ping_timeout"] == config.PING_TIMEOUT_S

    await client.close()


@pytest.mark.integration
async def test_connect_timeout_si_el_handshake_cuelga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: un connector que nunca resuelve + timeout de arranque corto.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    monkeypatch.setattr(config, "CONNECT_TIMEOUT_S", 0.05)

    async def hanging_connector(
        url: str, *, headers: Any, ping_interval: Any, ping_timeout: Any
    ) -> Any:
        await asyncio.sleep(3600)

    client = OpenAIRealtimeClient(connector=hanging_connector)

    # Act + Assert: el arranque no cuelga indefinidamente, corta por timeout
    with pytest.raises(asyncio.TimeoutError):
        await client.connect()

    await client.close()


@pytest.mark.integration
async def test_close_durante_open_cierra_el_ws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: connector lento (pausable). close() corre MIENTRAS _open() espera
    # el WS. El socket que termina de abrirse NO debe quedar huérfano.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")
    ws_requested = asyncio.Event()
    release = asyncio.Event()
    created_ws = FakeRealtimeWS(messages=[])

    async def slow_connector(
        url: str, *, headers: Any, ping_interval: Any, ping_timeout: Any
    ) -> Any:
        ws_requested.set()
        await release.wait()
        return created_ws

    client = OpenAIRealtimeClient(connector=slow_connector)
    connect_task = asyncio.create_task(client.connect())
    await ws_requested.wait()  # _open() está esperando el WS

    # Act: cerrar mientras _open() está en vuelo, luego dejar volver al connector
    await client.close()
    release.set()
    with contextlib.suppress(Exception):
        await connect_task
    await asyncio.sleep(0.01)

    # Assert: el WS que se abrió igual fue cerrado (no leak)
    assert created_ws.closed is True


@pytest.mark.unit
def test_backoff_crece_con_los_intentos_y_se_capa() -> None:
    from norivy_kiosk.realtime.client import _backoff_delay

    # base = min(min_s * 2**attempt, max_s), jitter ±25%
    d0 = _backoff_delay(0, 1.0, 100.0)  # base 1.0  → [0.75, 1.25]
    d3 = _backoff_delay(3, 1.0, 100.0)  # base 8.0  → [6.0, 10.0]
    capped = _backoff_delay(20, 1.0, 5.0)  # base cap 5.0 → [3.75, 6.25]

    assert 0.75 <= d0 <= 1.25
    assert 6.0 <= d3 <= 10.0
    assert 3.75 <= capped <= 6.25


@pytest.mark.unit
def test_backoff_no_desborda_en_outage_largo() -> None:
    from norivy_kiosk.realtime.client import _backoff_delay

    # attempt gigante (outage de horas → attempt sube sin techo): NO debe lanzar
    # OverflowError con 2**attempt; el delay se capea a max_s.
    delay = _backoff_delay(2000, 0.5, 8.0)
    assert 6.0 <= delay <= 10.0  # capado a 8.0 ± 25%


@pytest.mark.integration
async def test_reconexion_mid_speech_limpia_estado_hablando(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: el 1er WS entrega response.created (Norivy arranca a hablar) y cae.
    # El estado "hablando" NO debe quedar colgado: el supervisor debe emitir un
    # speech_done sintético y resetear _response_active al reconectar.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-fake")

    def ws_for_call(n: int) -> Any:
        if n == 1:
            return FakeRealtimeWS(
                messages=[json.dumps({"type": "response.created"})]
            )
        return BlockingWS()

    connector = RecordingConnector(ws_for_call)
    client = OpenAIRealtimeClient(
        connector=connector, reconnect_min_s=0.01, reconnect_max_s=0.02
    )

    seen_speech_done = asyncio.Event()

    async def watch() -> None:
        while True:
            event = await client.events.get()
            if event.get("kind") == "speech_done":
                seen_speech_done.set()
                return

    watcher = asyncio.create_task(watch())

    # Act
    await client.connect()

    # Assert: llega un speech_done sintético para sellar el estado "hablando"
    await asyncio.wait_for(seen_speech_done.wait(), timeout=2.0)
    # y el estado interno quedó limpio tras reconectar
    await asyncio.sleep(0.05)
    assert client._response_active is False

    await client.close()
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher
