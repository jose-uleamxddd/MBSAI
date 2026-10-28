"""TDD B1 — ciclo de vida de RealtimeSession (start/stop robustos).

Comentario en español: si ``start()`` falla DESPUÉS de que el cliente ya
conectó (y por ende ya lanzó su supervisor de reconexión), la sesión debe
cerrar el cliente igual — si no, queda un supervisor huérfano reconectando a
OpenAI en loop, quemando cuota, invisible al operador.
"""

from __future__ import annotations

import asyncio

import pytest

from norivy_kiosk.hub import Hub
from norivy_kiosk.ratelimit import RateLimiter
from norivy_kiosk.realtime.session import RealtimeSession


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class GreetSpyClient:
    """Cliente espía que cuenta los create_greeting (para el rate limit)."""

    def __init__(self) -> None:
        self.greets = 0
        self._events: asyncio.Queue[dict] = asyncio.Queue()

    @property
    def events(self) -> asyncio.Queue[dict]:
        return self._events

    @property
    def connected(self) -> bool:
        return True

    async def create_greeting(self) -> None:
        self.greets += 1

    async def set_mic_listening(self, listening: bool) -> None:
        pass

    async def close(self) -> None:
        pass


class SpyClient:
    """Doble del cliente Realtime: registra si fue cerrado; falla en listening."""

    def __init__(self, *, fail_on_listening: bool = False) -> None:
        self.connected = False
        self.closed = False
        self._fail_on_listening = fail_on_listening
        self._events: asyncio.Queue[dict] = asyncio.Queue()

    @property
    def events(self) -> asyncio.Queue[dict]:
        return self._events

    async def connect(self) -> None:
        self.connected = True  # simula que el supervisor ya arrancó

    async def set_mic_listening(self, listening: bool) -> None:
        if self._fail_on_listening:
            raise RuntimeError("WS cayó justo después de connect()")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.integration
async def test_start_a_medias_cierra_el_cliente() -> None:
    # Arrange: el cliente conecta OK pero set_mic_listening falla (blip de red).
    client = SpyClient(fail_on_listening=True)
    session = RealtimeSession(Hub(), client=client)

    # Act + Assert: start() propaga el error PERO cierra el cliente antes.
    with pytest.raises(RuntimeError):
        await session.start()

    assert client.closed is True  # no queda supervisor huérfano


@pytest.mark.integration
async def test_stop_cierra_el_cliente_aunque_no_este_activa() -> None:
    # Un cliente pudo haber conectado (supervisor vivo) sin que la sesión llegara
    # a _active=True. stop() debe cerrarlo igual (idempotente, incondicional).
    client = SpyClient()
    session = RealtimeSession(Hub(), client=client)
    await client.connect()

    await session.stop()

    assert client.closed is True


@pytest.mark.integration
async def test_begin_session_respeta_el_rate_limit() -> None:
    # Arrange: spam de begin_session no debe disparar N response.create (DoS económico).
    client = GreetSpyClient()
    clock = _FakeClock()
    limiter = RateLimiter(min_interval_s=3.0, clock=clock)
    session = RealtimeSession(Hub(), client=client, greet_limiter=limiter)

    # Act + Assert
    await session.begin_session()  # primero pasa
    await session.begin_session()  # inmediato: bloqueado por rate limit
    assert client.greets == 1

    clock.advance(3.0)
    await session.begin_session()  # ya pasó el intervalo
    assert client.greets == 2
