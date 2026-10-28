"""TDD — MicCapture.start(): validación de device + cola acotada con drops.

Comentario en español: `start()` abre el stream de sounddevice. Antes de abrir
debe validar que el device soporte 24kHz nativo (si no, PortAudio resamplea y la
transcripción puede degradar → warning VISIBLE, sin abortar). El hardware se
MOCKEA por completo (`sd` inyectado) — nunca tocamos un mic real.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from norivy_kiosk import config
from norivy_kiosk.realtime import audio as audio_mod
from norivy_kiosk.realtime.audio import MicCapture


class _FakeRawInputStream:
    """Doble de sd.RawInputStream: no toca hardware, arranca 'activo'."""

    def __init__(self, **_kwargs: Any) -> None:
        self.active = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


class _FakeSounddevice:
    """Doble del módulo `sounddevice`: check_input_settings controlable."""

    RawInputStream = _FakeRawInputStream

    def __init__(self, *, check_raises: Exception | None = None) -> None:
        self._check_raises = check_raises
        self.check_calls: list[dict[str, Any]] = []

    def check_input_settings(self, **kwargs: Any) -> None:
        self.check_calls.append(kwargs)
        if self._check_raises is not None:
            raise self._check_raises


def _make_mic() -> MicCapture:
    loop = asyncio.new_event_loop()
    return MicCapture(sample_rate=24000, channels=1, device=None, loop=loop)


@pytest.mark.unit
def test_start_avisa_si_el_device_no_soporta_el_samplerate(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange: el device NO soporta 24kHz nativo (check_input_settings falla) —
    # debe avisar VISIBLE (resample implícito degrada) pero abrir igual.
    fake_sd = _FakeSounddevice(
        check_raises=ValueError("Invalid sample rate; device runs at 48000")
    )
    monkeypatch.setattr(audio_mod, "sd", fake_sd)
    mic = _make_mic()

    # Act
    with caplog.at_level(logging.WARNING, logger="norivy_kiosk.realtime.audio"):
        mic.start()

    # Assert: warning emitido Y stream igual abierto (no abortó)
    assert fake_sd.check_calls, "debe validar el device antes de abrir"
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)
    assert mic.is_alive is True


@pytest.mark.unit
def test_start_no_avisa_si_el_device_soporta_el_samplerate(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Device OK: check_input_settings no falla → sin warning de resample.
    fake_sd = _FakeSounddevice(check_raises=None)
    monkeypatch.setattr(audio_mod, "sd", fake_sd)
    mic = _make_mic()

    with caplog.at_level(logging.WARNING, logger="norivy_kiosk.realtime.audio"):
        mic.start()

    assert not any(
        "resamplea" in rec.getMessage() or "48" in rec.getMessage()
        for rec in caplog.records
    )
    assert mic.is_alive is True


class _ExplodingSounddevice(_FakeSounddevice):
    """sounddevice cuyo RawInputStream revienta al construirse (device ocupado)."""

    class _BoomStream:
        def __init__(self, **_kwargs: Any) -> None:
            raise OSError("device ocupado por otra app (Zoom/OBS)")

    RawInputStream = _BoomStream


@pytest.mark.unit
def test_start_es_idempotente_no_reabre_si_ya_hay_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Segundo start() con un stream ya abierto NO debe re-validar ni re-abrir
    # (early-return): el watchdog llama start() de nuevo y no debe duplicar streams.
    fake_sd = _FakeSounddevice(check_raises=None)
    monkeypatch.setattr(audio_mod, "sd", fake_sd)
    mic = _make_mic()

    mic.start()
    first_stream = mic._stream
    mic.start()  # segundo start: debe ser no-op

    assert mic._stream is first_stream
    assert len(fake_sd.check_calls) == 1  # no re-validó el device


@pytest.mark.unit
def test_start_no_crashea_si_el_device_no_abre(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # El device está ocupado/inválido y RawInputStream revienta: start() NO debe
    # propagar (Norivy igual puede saludar sin mic); loguea error y deja el mic
    # 'no vivo' para que el watchdog lo detecte.
    fake_sd = _ExplodingSounddevice(check_raises=None)
    monkeypatch.setattr(audio_mod, "sd", fake_sd)
    mic = _make_mic()

    with caplog.at_level(logging.ERROR, logger="norivy_kiosk.realtime.audio"):
        mic.start()  # no debe propagar

    assert mic.is_alive is False
    assert mic._stream is None
    assert any(rec.levelno == logging.ERROR for rec in caplog.records)


@pytest.mark.unit
def test_stop_sin_stream_es_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    # stop() sin haber abierto nunca (stream None) es un no-op seguro (idempotente).
    fake_sd = _FakeSounddevice(check_raises=None)
    monkeypatch.setattr(audio_mod, "sd", fake_sd)
    mic = _make_mic()

    mic.stop()  # no debe lanzar
    assert mic._stream is None


@pytest.mark.unit
def test_stop_traga_el_error_de_cierre_y_deja_el_stream_none(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Si stream.stop()/close() revienta al cerrar, stop() lo traga (cleanup) y
    # deja _stream en None igual — no dejar un stream zombie tras un cierre sucio.
    fake_sd = _FakeSounddevice(check_raises=None)
    monkeypatch.setattr(audio_mod, "sd", fake_sd)
    mic = _make_mic()
    mic.start()

    def boom() -> None:
        raise RuntimeError("PortAudio se cayó al cerrar")

    assert mic._stream is not None
    mic._stream.stop = boom  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="norivy_kiosk.realtime.audio"):
        mic.stop()  # no debe propagar

    assert mic._stream is None
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


@pytest.mark.unit
def test_cola_del_mic_usa_el_max_de_config() -> None:
    # La cola del mic debe estar acotada a config.MIC_QUEUE_MAX (no 256 hardcoded)
    # para no arrastrar segundos de lag bajo backpressure de red.
    mic = _make_mic()
    assert mic.queue.maxsize == config.MIC_QUEUE_MAX


@pytest.mark.unit
async def test_saturar_la_cola_descarta_el_mas_viejo_y_cuenta_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: cola chica para forzar backpressure sin encolar decenas.
    monkeypatch.setattr(config, "MIC_QUEUE_MAX", 3)
    loop = asyncio.get_running_loop()
    mic = MicCapture(sample_rate=24000, channels=1, device=None, loop=loop)

    # Act: encolar 5 chunks en una cola de cap 3 → 2 descartes (los más viejos).
    for i in range(5):
        mic._enqueue(f"chunk{i}".encode())

    # Assert: contador de drops sube y el frame más viejo se descartó.
    assert mic.dropped == 2
    assert mic.queue.qsize() == 3
    oldest = mic.queue.get_nowait()
    assert oldest == b"chunk2"  # chunk0 y chunk1 se descartaron
