"""TDD — bombas de la sesión (_pump_mic / _pump_events), start degradado, mute.

Comentario en español: cubre el CORAZÓN del bridge de la sesión: `_pump_mic`
(cola del mic → cliente, solo si no muteado) y `_pump_events` (eventos del
cliente → Hub), más el arranque degradado cuando el mic no abre y el mute con
mic presente. Todo con fakes (cliente + mic) — nunca red ni hardware real.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from norivy_kiosk import config
from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime import audio as audio_mod
from norivy_kiosk.realtime.session import RealtimeSession


class _RecordingHub(Hub):
    """Hub que captura los frames en vez de mandarlos por red."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, Any]] = []

    async def broadcast(self, message: dict[str, Any]) -> None:
        self.frames.append(message)


class _AppendSpyClient:
    """Cliente fake: registra el audio que le mandan; expone una cola de eventos."""

    def __init__(self) -> None:
        self.appended: list[bytes] = []
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @property
    def events(self) -> asyncio.Queue[dict[str, Any]]:
        return self._events

    @property
    def connected(self) -> bool:
        return True

    @property
    def is_speaking(self) -> bool:
        return False

    async def connect(self) -> None:
        pass

    async def append_audio(self, pcm16: bytes) -> None:
        self.appended.append(pcm16)

    async def set_mic_listening(self, listening: bool) -> None:
        pass

    async def commit_and_respond(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _DeadStream:
    """Stream de sounddevice que NO arranca: `active` queda False (device muerto)."""

    def __init__(self, **_kwargs: Any) -> None:
        self.active = False

    def start(self) -> None:
        # No revive: simula un device que abre pero queda inactivo.
        self.active = False

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


class _DeadSd:
    """sounddevice fake cuyo stream nunca queda activo (mic no disponible)."""

    RawInputStream = _DeadStream

    @staticmethod
    def check_input_settings(**_kwargs: Any) -> None:
        pass


# --------------------------------------------------------------------------- #
# _pump_mic                                                                    #
# --------------------------------------------------------------------------- #


class _FakeMicQueue:
    """Mic mínimo: solo expone una cola precargada para el _pump_mic."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for chunk in chunks:
            self._queue.put_nowait(chunk)

    @property
    def queue(self) -> asyncio.Queue[bytes]:
        return self._queue


async def _drain_pump(
    session: RealtimeSession, coro: Any, delay: float = 0.05
) -> None:
    """Corre una bomba (loop `while self._active`), la frena bajando `_active` y la
    CANCELA — así no queda esperando el `wait_for` interno de la cola (1s), que bajo
    la carga de la suite completa volvía el test flaky."""
    task = asyncio.create_task(coro)
    await asyncio.sleep(delay)
    session._active = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.integration
async def test_pump_mic_reenvia_audio_al_cliente_si_no_muteado() -> None:
    # Con el mic DESmuteado, cada chunk de la cola del mic se manda al cliente.
    client = _AppendSpyClient()
    session = RealtimeSession(Hub(), client=client)
    session._mic = _FakeMicQueue([b"\x01\x02", b"\x03\x04"])  # type: ignore[assignment]
    session._mic_muted = False
    session._active = True

    await _drain_pump(session, session._pump_mic())

    assert client.appended == [b"\x01\x02", b"\x03\x04"]


@pytest.mark.integration
async def test_pump_mic_descarta_audio_si_esta_muteado() -> None:
    # Muteado: los chunks se drenan pero NO se mandan al cliente (Norivy no escucha).
    client = _AppendSpyClient()
    session = RealtimeSession(Hub(), client=client)
    session._mic = _FakeMicQueue([b"\x01\x02"])  # type: ignore[assignment]
    session._mic_muted = True
    session._active = True

    await _drain_pump(session, session._pump_mic())

    assert client.appended == []


@pytest.mark.integration
async def test_pump_mic_no_muere_si_append_falla() -> None:
    # Si append_audio revienta (WS cayó), el pump loguea y SIGUE (no muere el loop).
    class _BoomClient(_AppendSpyClient):
        async def append_audio(self, pcm16: bytes) -> None:
            raise RuntimeError("append reventó")

    client = _BoomClient()
    session = RealtimeSession(Hub(), client=client)
    session._mic = _FakeMicQueue([b"\x01\x02"])  # type: ignore[assignment]
    session._mic_muted = False
    session._active = True

    # No debe propagar: el except del pump lo traga.
    await _drain_pump(session, session._pump_mic())


# --------------------------------------------------------------------------- #
# _pump_events                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_pump_events_traduce_eventos_del_cliente_al_hub() -> None:
    # Un evento normalizado del cliente (transcript) se traduce al frame WS del Hub.
    hub = _RecordingHub()
    client = _AppendSpyClient()
    client.events.put_nowait({"kind": "transcript", "delta": "hola"})
    session = RealtimeSession(hub, client=client)
    session._active = True

    await _drain_pump(session, session._pump_events())

    assert {"type": "transcript", "delta": "hola"} in hub.frames


# --------------------------------------------------------------------------- #
# start() degradado (mic no abre) + set_mic_muted con mic                      #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_start_avisa_si_el_mic_no_abre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # El cliente conecta OK pero el mic NO abre (device muerto): la sesión igual
    # arranca (Norivy puede saludar) pero avisa VISIBLE — error + status mic_error.
    monkeypatch.setattr(audio_mod, "sd", _DeadSd())
    monkeypatch.setattr(config, "MIC_WATCHDOG_INTERVAL_S", 0.01)
    hub = _RecordingHub()
    session = RealtimeSession(hub, client=_AppendSpyClient())

    await session.start()
    try:
        assert session.active is True  # arrancó igual
        types = [f.get("type") for f in hub.frames]
        assert "error" in types
        states = [f.get("state") for f in hub.frames if f.get("type") == "status"]
        assert "mic_error" in states
    finally:
        await session.stop()


@pytest.mark.integration
async def test_start_es_idempotente() -> None:
    # Segundo start() con la sesión ya activa es un no-op (early-return), no
    # re-conecta ni duplica tareas.
    client = _AppendSpyClient()
    session = RealtimeSession(Hub(), client=client)
    session._active = True  # simula sesión ya activa

    await session.start()  # debe retornar de inmediato

    assert session._tasks == []  # no lanzó tareas nuevas


@pytest.mark.integration
async def test_set_mic_muted_actualiza_el_mic_presente() -> None:
    # set_mic_muted con un mic presente debe propagar el mute al MicCapture.
    class _MuteSpyMic:
        def __init__(self) -> None:
            self.muted: bool | None = None

        def set_muted(self, muted: bool) -> None:
            self.muted = muted

    hub = _RecordingHub()
    mic = _MuteSpyMic()
    session = RealtimeSession(hub, client=_AppendSpyClient())
    session._mic = mic  # type: ignore[assignment]

    await session.set_mic_muted(True)

    assert mic.muted is True
    assert {"type": "mic_state", "muted": True} in hub.frames
