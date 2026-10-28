"""TDD FIX 5 — watchdog del mic: re-abre y avisa si el mic muere en runtime.

Comentario en español: si el mic USB se desconecta o Zoom/OBS se lo roba a mitad
del evento, el stream muere (`is_alive` → False). El watchdog debe (a) intentar
re-abrirlo una vez; (b) si revive, avisar al renderer con `mic_state{muted:...}`
(si quedó desmuteado, `muted:false` limpia el sticky `mic_error` de la pill);
(c) si sigue muerto, emitir `status{state:"mic_error"}` UNA sola vez por episodio
de caída (no spamear cada 4s). El mic se INYECTA (fake) — nunca hardware real.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from norivy_kiosk import config
from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime import audio as audio_mod
from norivy_kiosk.realtime.session import RealtimeSession


class FakeMic:
    """Doble de MicCapture: `is_alive` scriptable + cuenta los start()."""

    def __init__(self, *, alive_sequence: list[bool]) -> None:
        # `is_alive` va devolviendo cada valor de la secuencia (uno por lectura).
        self._alive_sequence = list(alive_sequence)
        self._alive = alive_sequence[0] if alive_sequence else False
        self.muted = False  # desmuteado: escuchando cuando muere.
        self.starts = 0

    @property
    def is_alive(self) -> bool:
        return self._alive

    def set_alive(self, value: bool) -> None:
        self._alive = value

    def start(self) -> None:
        self.starts += 1

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def stop(self) -> None:
        pass


class RecordingHub(Hub):
    """Hub que captura los frames broadcasteados en vez de mandarlos por red."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, Any]] = []

    async def broadcast(self, message: dict[str, Any]) -> None:
        self.frames.append(message)


def _states(frames: list[dict[str, Any]]) -> list[str]:
    return [f.get("state") for f in frames if f.get("type") == "status"]


@pytest.mark.integration
async def test_watchdog_reintenta_start_cuando_el_mic_muere() -> None:
    # Arrange: el mic está muerto en el tick; el fake NO revive tras start().
    hub = RecordingHub()
    session = RealtimeSession(hub)
    mic = FakeMic(alive_sequence=[False])
    session._mic = mic  # type: ignore[assignment]

    # Act
    await session._watchdog_tick()

    # Assert: intentó re-abrir UNA vez y emitió mic_error (sigue muerto).
    assert mic.starts == 1
    assert "mic_error" in _states(hub.frames)


@pytest.mark.integration
async def test_watchdog_avisa_mic_state_cuando_el_mic_revive() -> None:
    # Arrange: muerto en la primera lectura; tras start() revive (desmuteado).
    hub = RecordingHub()
    session = RealtimeSession(hub)
    mic = FakeMic(alive_sequence=[False])
    session._mic = mic  # type: ignore[assignment]
    session._mic_muted = False  # el mic estaba abierto cuando murió.

    # start() del fake lo revive: simulamos el efecto.
    def revive() -> None:
        mic.starts += 1
        mic.set_alive(True)

    mic.start = revive  # type: ignore[method-assign]

    # Act
    await session._watchdog_tick()

    # Assert: NO mic_error; sí un mic_state{muted:false} (limpia sticky en pill).
    assert "mic_error" not in _states(hub.frames)
    mic_states = [f for f in hub.frames if f.get("type") == "mic_state"]
    assert mic_states, "debe emitir mic_state al recuperar el mic"
    assert mic_states[-1]["muted"] is False


@pytest.mark.integration
async def test_watchdog_no_spamea_mic_error_por_episodio() -> None:
    # Arrange: mic muerto y NO revive; dos ticks seguidos (episodio único).
    hub = RecordingHub()
    session = RealtimeSession(hub)
    mic = FakeMic(alive_sequence=[False])
    session._mic = mic  # type: ignore[assignment]

    # Act: dos chequeos del watchdog sin que el mic se recupere.
    await session._watchdog_tick()
    await session._watchdog_tick()

    # Assert: el mic_error se emitió UNA sola vez (no un banner cada 4s).
    assert _states(hub.frames).count("mic_error") == 1


@pytest.mark.integration
async def test_watchdog_no_hace_nada_si_el_mic_esta_vivo() -> None:
    # Mic sano: el watchdog no intenta re-abrir ni emite nada.
    hub = RecordingHub()
    session = RealtimeSession(hub)
    mic = FakeMic(alive_sequence=[True])
    session._mic = mic  # type: ignore[assignment]

    await session._watchdog_tick()

    assert mic.starts == 0
    assert hub.frames == []


@pytest.mark.integration
async def test_watchdog_resetea_episodio_tras_recuperar() -> None:
    # Arrange: muere → mic_error; revive → mic_state; vuelve a morir → mic_error
    # de NUEVO (es un episodio nuevo, no el mismo silenciado).
    hub = RecordingHub()
    session = RealtimeSession(hub)
    mic = FakeMic(alive_sequence=[False])
    session._mic = mic  # type: ignore[assignment]
    session._mic_muted = False

    # Tick 1: muerto, no revive → mic_error.
    await session._watchdog_tick()
    assert _states(hub.frames).count("mic_error") == 1

    # Tick 2: sigue muerto pero ahora start() lo revive → mic_state.
    def revive() -> None:
        mic.set_alive(True)

    mic.start = revive  # type: ignore[method-assign]
    await session._watchdog_tick()

    # Tick 3: vuelve a morir → mic_error otra vez (episodio nuevo).
    mic.set_alive(False)
    mic.start = lambda: None  # type: ignore[method-assign,assignment]
    await session._watchdog_tick()
    assert _states(hub.frames).count("mic_error") == 2


class _FakeStream:
    """Stream de sounddevice sin hardware; arranca 'activo'."""

    def __init__(self, **_kwargs: Any) -> None:
        self.active = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


class _FakeSd:
    """Doble del módulo sounddevice para start()/stop() del mic real, sin hardware."""

    RawInputStream = _FakeStream

    @staticmethod
    def check_input_settings(**_kwargs: Any) -> None:
        pass


class _StartStopClient:
    """Cliente mínimo para start()/stop() de la sesión, sin red."""

    def __init__(self) -> None:
        self.connected = False
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @property
    def events(self) -> asyncio.Queue[dict[str, Any]]:
        return self._events

    async def connect(self) -> None:
        self.connected = True

    async def set_mic_listening(self, listening: bool) -> None:
        pass

    async def close(self) -> None:
        self.connected = False


@pytest.mark.integration
async def test_start_lanza_el_watchdog_y_stop_lo_cancela(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: sin hardware ni red; intervalo acelerado para que el tick corra ya.
    monkeypatch.setattr(audio_mod, "sd", _FakeSd())
    monkeypatch.setattr(config, "MIC_WATCHDOG_INTERVAL_S", 0.01)
    session = RealtimeSession(Hub(), client=_StartStopClient())

    # Act: arrancar la sesión → el watchdog queda corriendo.
    await session.start()
    running = [t for t in session._tasks if not t.done()]
    assert len(running) >= 3  # pump_mic + pump_events + watchdog

    # Dejar correr algunos ticks del watchdog (mic sano: no hace nada, no crashea).
    await asyncio.sleep(0.05)

    # Assert: stop() cancela TODAS las tareas (watchdog incluido).
    await session.stop()
    assert all(t.done() for t in session._tasks) or session._tasks == []
