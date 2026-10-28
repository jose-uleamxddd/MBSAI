"""TDD B1 — MicCapture robusto en shutdown.

Comentario en español: el callback de sounddevice corre en su propio hilo y
agenda el encolado con ``call_soon_threadsafe``. Si el event loop ya se cerró
(shutdown abnormal) mientras el stream sigue vivo, eso lanza ``RuntimeError``
dentro del hilo de audio — hay que tragarlo, no dejar que rompa el hilo.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from norivy_kiosk.realtime.audio import MicCapture


@pytest.mark.unit
def test_on_audio_no_lanza_si_el_loop_esta_cerrado() -> None:
    # Arrange: un loop ya cerrado (simula shutdown abnormal con el mic aún vivo).
    loop = asyncio.new_event_loop()
    loop.close()
    mic = MicCapture(sample_rate=24000, channels=1, device=None, loop=loop)
    mic.set_muted(False)

    # Act + Assert: el callback NO debe propagar RuntimeError del loop cerrado.
    mic._on_audio(b"\x00\x00", 1, None, None)


class _FakeStream:
    """Doble de sd.RawInputStream con `.active` controlable (unplug/CallbackAbort)."""

    def __init__(self, *, active: bool) -> None:
        self.active = active


@pytest.mark.unit
def test_is_alive_false_si_el_stream_murio() -> None:
    # Arrange: un stream que existe pero se abortó (active=False) por unplug del
    # mic o CallbackAbort — NO debe reportar "vivo" para siempre.
    loop = asyncio.new_event_loop()
    try:
        mic = MicCapture(sample_rate=24000, channels=1, device=None, loop=loop)
        mic._stream = _FakeStream(active=False)  # type: ignore[assignment]

        # Act + Assert
        assert mic.is_alive is False
    finally:
        loop.close()


@pytest.mark.unit
def test_is_alive_true_si_el_stream_esta_activo() -> None:
    loop = asyncio.new_event_loop()
    try:
        mic = MicCapture(sample_rate=24000, channels=1, device=None, loop=loop)
        mic._stream = _FakeStream(active=True)  # type: ignore[assignment]

        assert mic.is_alive is True
    finally:
        loop.close()


class _FakeStatus:
    """Doble de sd.CallbackFlags: truthy con flags de over/underflow controlables."""

    def __init__(
        self, *, input_overflow: bool = False, input_underflow: bool = False
    ) -> None:
        self.input_overflow = input_overflow
        self.input_underflow = input_underflow

    def __bool__(self) -> bool:
        return self.input_overflow or self.input_underflow

    def __str__(self) -> str:
        return "input overflow" if self.input_overflow else "input underflow"


@pytest.mark.unit
def test_overflow_del_mic_se_loguea_como_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange: un overflow del mic (pierde frames) debe ser VISIBLE al operador,
    # no enterrado en DEBUG (invisible bajo basicConfig(INFO)).
    loop = asyncio.new_event_loop()
    try:
        mic = MicCapture(sample_rate=24000, channels=1, device=None, loop=loop)
        status = _FakeStatus(input_overflow=True)

        # Act
        with caplog.at_level(logging.WARNING, logger="norivy_kiosk.realtime.audio"):
            mic._on_audio(b"\x00\x00", 1, None, status)

        # Assert: quedó un WARNING sobre el mic
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)
    finally:
        loop.close()
