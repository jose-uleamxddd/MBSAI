"""Captura de micrófono (PCM16 mono 24 kHz) hacia una asyncio.Queue.

Comentario en español: `MicCapture` corre el stream bloqueante de sounddevice en
su propio hilo y empuja los chunks PCM16 a una cola asíncrona usando
`call_soon_threadsafe`. Cuando está muteado, descarta los frames (no encola).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import sounddevice as sd

from .. import config

logger = logging.getLogger(__name__)

# Tamaño de bloque ~21 ms a 24 kHz (512 samples). Balance latencia/overhead.
_BLOCKSIZE_SAMPLES = 512
_DTYPE = "int16"  # PCM16 little-endian nativo del modelo Realtime.

# Cada cuántos descartes de cola loguear un warning (evita spamear el log). Mismo
# criterio que la cola de eventos del cliente (`_DROP_LOG_EVERY` en client.py).
_DROP_LOG_EVERY = 50


class MicCapture:
    """Stream de mic en un hilo; entrega chunks PCM16 a una asyncio.Queue."""

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        device: str | int | None,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._device = device
        self._loop = loop
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=config.MIC_QUEUE_MAX
        )
        self._dropped = 0
        self._stream: sd.RawInputStream | None = None
        self._muted = True  # arranca muteado: sin frames hasta abrir el mic.

    @property
    def queue(self) -> asyncio.Queue[bytes]:
        return self._queue

    @property
    def dropped(self) -> int:
        """Frames descartados por backpressure de la cola (observabilidad)."""
        return self._dropped

    @property
    def is_alive(self) -> bool:
        """True solo si el stream está abierto Y activo (observabilidad).

        ``RawInputStream.active`` pasa a ``False`` cuando el stream se aborta
        (unplug del mic / ``CallbackAbort``): sin ese chequeo, un stream muerto
        reportaría ``True`` para siempre y el watchdog nunca lo re-abriría.
        """
        return self._stream is not None and self._stream.active

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        logger.info("[mic] %s", "muteado" if muted else "abierto")

    def _on_audio(self, indata: bytes, frames: int, time: Any, status: Any) -> None:
        """Callback del hilo de sounddevice — corre fuera del event loop."""
        if status:
            # Over/underflow = el mic está PERDIENDO frames: warning VISIBLE (un
            # DEBUG queda invisible bajo basicConfig(INFO) y el operador no se
            # entera de que la voz llega recortada). El resto del status → DEBUG.
            if status.input_overflow or status.input_underflow:
                logger.warning("[mic] mic perdiendo frames: %s", status)
            else:
                logger.debug("[mic] status sounddevice: %s", status)
        if self._muted:
            return
        chunk = bytes(indata)
        # El loop puede estar cerrado en shutdown con el stream aún vivo:
        # descartar el chunk en silencio (aceptable en cierre).
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._enqueue, chunk)

    def _enqueue(self, chunk: bytes) -> None:
        """Encola en el loop; si está llena, descarta el más viejo (anti-lag).

        El descarte se CONTABILIZA (``dropped``) y se loguea cada cierto número:
        un overflow sostenido = el pipeline no da abasto, deuda de resiliencia
        que no debe perderse en silencio.
        """
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self._dropped += 1
                if self._dropped % _DROP_LOG_EVERY == 1:
                    logger.warning(
                        "[mic] cola saturada: %d frames descartados", self._dropped
                    )
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(chunk)

    def _check_device_samplerate(self) -> None:
        """Avisa (sin abortar) si el device no soporta el samplerate nativo.

        Si PortAudio rechaza los settings (device a 48kHz), resamplea implícito a
        24kHz: funciona, pero puede degradar la transcripción. El warning es
        VISIBLE para que el operador lo sepa; el stream se abre igual.
        """
        try:
            sd.check_input_settings(
                device=self._device,
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype=_DTYPE,
            )
        except Exception as exc:  # noqa: BLE001 — boundary del device
            logger.warning(
                "[mic] el device no soporta %d Hz nativo (%s) — PortAudio "
                "resamplea, puede degradar la transcripción",
                self._sample_rate,
                exc,
            )

    def start(self) -> None:
        """Abre el stream de entrada; loguea y no crashea si el device falla."""
        if self._stream is not None:
            return
        self._check_device_samplerate()
        try:
            self._stream = sd.RawInputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype=_DTYPE,
                blocksize=_BLOCKSIZE_SAMPLES,
                device=self._device,
                callback=self._on_audio,
            )
            self._stream.start()
            logger.info(
                "[mic] stream abierto (%d Hz, %d canal, device=%s)",
                self._sample_rate,
                self._channels,
                self._device if self._device is not None else "default",
            )
        except Exception as exc:  # noqa: BLE001 — boundary del device
            logger.error("[mic] no se pudo abrir el stream: %s", exc)
            self._stream = None

    def stop(self) -> None:
        """Cierra el stream del mic de forma segura."""
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as exc:  # noqa: BLE001 — cleanup
            logger.warning("[mic] error cerrando stream: %s", exc)
        finally:
            self._stream = None
            logger.info("[mic] stream cerrado")
