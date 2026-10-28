"""RealtimeSession — puentea mic ↔ cliente OpenAI ↔ tools ↔ Hub local.

Comentario en español: orquesta la sesión speech-to-speech del kiosco. Arranca el
cliente Realtime y el mic, corre tareas (mic→cliente, eventos→Hub, watchdog) y
traduce los eventos del cliente al PROTOCOLO WS que consume el renderer Electron.
El mic arranca MUTEADO: el cliente toca la pantalla (``begin_session``) para abrir
el mic y recibir el saludo; al terminar (o por inactividad) ``end_session`` borra
la conversación y el carrito para que el siguiente cliente empiece limpio.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from .. import config
from ..hub import Hub
from ..ratelimit import RateLimiter
from ..tools import ToolRunner, build_runner
from .audio import MicCapture
from .client import OpenAIRealtimeClient

logger = logging.getLogger(__name__)


def _pcm16_b64_seconds(pcm16_b64: str) -> float:
    """Duración (s) de un chunk PCM16 mono @ MIC_SAMPLE_RATE dado su base64."""
    n_bytes = len(pcm16_b64) * 3 // 4  # base64 → bytes (aprox, sin padding exacto)
    n_samples = n_bytes // 2  # PCM16 = 2 bytes por sample
    return n_samples / config.MIC_SAMPLE_RATE


class RealtimeSession:
    """Sesión de vida del evento: cliente Realtime + mic + bridge al Hub."""

    def __init__(
        self,
        hub: Hub,
        *,
        client: OpenAIRealtimeClient | None = None,
        runner: ToolRunner | None = None,
        greet_limiter: RateLimiter | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._hub = hub
        self._client = client or OpenAIRealtimeClient()
        # Runner de tools: si no se inyecta, se arma en start() con las credenciales
        # del .env (así un .env incompleto falla visible al arrancar, no al importar).
        self._runner = runner
        self._tool_tasks: set[asyncio.Task[None]] = set()
        self._mic: MicCapture | None = None
        self._mic_muted = True  # arranca muteado.
        self._tasks: list[asyncio.Task[None]] = []
        self._active = False
        # True mientras dura UN episodio de caída del mic: evita re-emitir el
        # banner mic_error cada tick (4s). Se resetea al recuperar el mic.
        self._mic_error_reported = False
        self._greet_limiter = greet_limiter or RateLimiter(
            config.GREET_MIN_INTERVAL_S
        )
        # Half-duplex: reloj (inyectable en tests) + estimación de cuándo el audio
        # de Norivy termina de SONAR. Gateamos el mic hasta `_speaking_tail_until`
        # (fin estimado + margen) para no capturar su propia voz (feedback → bucle).
        self._clock = clock or monotonic
        self._speaking_tail_until = 0.0
        self._audio_start = 0.0  # cuándo empezó a sonar el audio de la respuesta
        self._audio_played_s = 0.0  # duración acumulada del audio de la respuesta

    @property
    def mic_muted(self) -> bool:
        return self._mic_muted

    @property
    def active(self) -> bool:
        return self._active

    @property
    def realtime_active(self) -> bool:
        """Propiedad CANÓNICA: la sesión arrancó Y el WS a OpenAI está vivo.

        Única fuente de verdad de "Norivy está realmente conectada". La usan
        TANTO ``hello()`` (renderer) COMO ``/healthz`` (operador) para no reportar
        verdades distintas: ``_active`` a secas mentiría "activo" con OpenAI caído.
        """
        return self._active and self._client.connected

    @property
    def openai_connected(self) -> bool:
        """True si el WS a OpenAI está vivo (observabilidad para /healthz)."""
        return self._client.connected

    @property
    def mic_alive(self) -> bool:
        """True si el stream de mic está abierto (observabilidad para /healthz)."""
        return self._mic is not None and self._mic.is_alive

    def hello(self) -> dict[str, Any]:
        """Snapshot inicial que el WS manda a cada renderer al conectarse."""
        return {
            "type": "hello",
            "character_name": config.CHARACTER_NAME,
            "store_name": config.STORE_NAME,
            "model": config.MODEL,
            "voice": config.VOICE,
            "mic_muted": self._mic_muted,
            "realtime_active": self.realtime_active,
        }

    async def start(self) -> None:
        """Conecta el cliente, abre el mic (muteado) y lanza las tareas-bridge.

        Si algo falla DESPUÉS de ``connect()`` (que ya lanzó el supervisor de
        reconexión), limpia el cliente/mic antes de re-lanzar — no dejar un
        supervisor huérfano reconectando a OpenAI en loop.
        """
        if self._active:
            return
        loop = asyncio.get_running_loop()
        try:
            if self._runner is None:
                self._runner = build_runner(self._hub.broadcast)
            await self._client.connect()
            await self._client.set_mic_listening(False)

            self._mic = MicCapture(
                sample_rate=config.MIC_SAMPLE_RATE,
                channels=config.MIC_CHANNELS,
                device=config.MIC_DEVICE,
                loop=loop,
            )
            self._mic.set_muted(True)
            self._mic.start()
            if not self._mic.is_alive:
                # El mic no abrió (device ocupado/inválido/sin permiso): avisar
                # VISIBLE — Norivy igual puede saludar, pero no escuchará.
                await self._hub.broadcast(
                    {"type": "error", "message": "Micrófono no disponible"}
                )
                await self._hub.broadcast(
                    {
                        "type": "status",
                        "state": "mic_error",
                        "detail": "mic no disponible",
                    }
                )

            self._tasks = [
                asyncio.create_task(self._pump_mic()),
                asyncio.create_task(self._pump_events()),
                asyncio.create_task(self._run_watchdog()),
            ]
            self._active = True
        except Exception:
            await self._cleanup()
            raise
        logger.info("[session] sesión Realtime activa (mic muteado)")

    def _should_gate_mic(self) -> bool:
        """Half-duplex: True mientras Norivy habla o su audio aún SUENA.

        Descartamos el mic mientras Norivy genera (``is_speaking``) Y hasta que su
        audio termina de sonar por los parlantes (``_speaking_tail_until``, estimado
        en ``_bcast_audio`` por la DURACIÓN real del audio — NO por cuándo OpenAI
        terminó de generar, que es mucho antes y dejaba reabrir el mic a mitad de la
        respuesta → capta su propia voz → bucle). Con parlantes NO hay barge-in.
        """
        if self._client.is_speaking:
            return True
        return self._clock() < self._speaking_tail_until

    async def _pump_mic(self) -> None:
        """Drena la cola del mic y reenvía PCM16 al cliente (solo si no muteado)."""
        assert self._mic is not None
        queue = self._mic.queue
        while self._active:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if self._mic_muted:
                continue
            # Half-duplex: no mandar el mic mientras Norivy habla (anti-feedback).
            if self._should_gate_mic():
                continue
            try:
                await self._client.append_audio(chunk)
            except Exception as exc:  # noqa: BLE001 — boundary
                logger.warning("[session] append_audio falló: %s", exc)

    async def _pump_events(self) -> None:
        """Drena los eventos del cliente y los traduce al protocolo WS compartido."""
        queue = self._client.events
        while self._active:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._broadcast_event(event)

    async def _run_watchdog(self) -> None:
        """Cada ``MIC_WATCHDOG_INTERVAL_S`` verifica que el mic siga vivo."""
        while self._active:
            await asyncio.sleep(config.MIC_WATCHDOG_INTERVAL_S)
            if not self._active:
                break
            await self._watchdog_tick()

    async def _watchdog_tick(self) -> None:
        """Un chequeo del mic: si murió, re-abrir una vez y avisar según resultado.

        Mic vivo → nada. Mic muerto (unplug / robado por Zoom-OBS) → ``start()``
        una vez; si revive, ``mic_state`` con el mute real (``muted:false`` limpia
        el sticky ``mic_error`` de la pill); si sigue muerto, ``mic_error`` sticky
        UNA vez por episodio (no spamear cada tick).
        """
        if self._mic is None or self._mic.is_alive:
            return
        logger.warning("[session] mic caído — intentando re-abrir")
        self._mic.start()
        if self._mic.is_alive:
            self._mic_error_reported = False
            await self._hub.broadcast(
                {"type": "mic_state", "muted": self._mic_muted}
            )
            logger.info("[session] mic recuperado (muted=%s)", self._mic_muted)
            return
        if not self._mic_error_reported:
            self._mic_error_reported = True
            await self._hub.broadcast(
                {"type": "status", "state": "mic_error", "detail": "mic no disponible"}
            )

    async def _broadcast_event(self, event: dict[str, Any]) -> None:
        """Traduce un evento normalizado del cliente al frame del protocolo WS.

        Despacha por tabla (``_EVENT_HANDLERS``) igual que ``_EXACT_EVENT_HANDLERS``
        en client.py: agregar un ``kind`` = un handler ``_bcast_*`` + una entrada.
        Un ``kind`` no mapeado se ignora en silencio (no rompe el bridge en vivo).
        """
        handler = _EVENT_HANDLERS.get(event.get("kind", ""))
        if handler is not None:
            await handler(self, event)

    async def _bcast_audio(self, event: dict[str, Any]) -> None:
        pcm16_b64 = event["pcm16_b64"]
        # Half-duplex: extendemos el gate del mic hasta que ESTE audio termine de
        # sonar. Fin ≈ inicio_de_reproducción + duración_total. OpenAI genera más
        # rápido de lo que suena, pero la duración del audio es fija (samples/rate).
        if self._audio_start == 0.0:
            self._audio_start = self._clock()
        self._audio_played_s += _pcm16_b64_seconds(pcm16_b64)
        self._speaking_tail_until = (
            self._audio_start + self._audio_played_s + config.HALF_DUPLEX_TAIL_S
        )
        await self._hub.broadcast({"type": "audio", "pcm16": pcm16_b64})

    async def _bcast_transcript(self, event: dict[str, Any]) -> None:
        await self._hub.broadcast({"type": "transcript", "delta": event["delta"]})

    async def _bcast_user_speech_started(self, event: dict[str, Any]) -> None:
        await self._hub.broadcast({"type": "user_speech_started"})
        await self._hub.broadcast(
            {"type": "status", "state": "listening", "detail": ""}
        )

    async def _bcast_user_transcript(self, event: dict[str, Any]) -> None:
        await self._hub.broadcast(
            {"type": "user_transcript", "delta": event["delta"]}
        )

    async def _bcast_user_transcript_done(self, event: dict[str, Any]) -> None:
        await self._hub.broadcast(
            {
                "type": "user_transcript_done",
                "transcript": event.get("transcript", ""),
            }
        )

    async def _bcast_speech_started(self, event: dict[str, Any]) -> None:
        # Nueva respuesta: reseteamos la estimación de duración del audio (half-duplex).
        self._audio_start = 0.0
        self._audio_played_s = 0.0
        await self._hub.broadcast({"type": "speech_started"})
        await self._hub.broadcast(
            {"type": "status", "state": "speaking", "detail": ""}
        )

    async def _bcast_speech_done(self, event: dict[str, Any]) -> None:
        await self._hub.broadcast({"type": "speech_done"})
        await self._hub.broadcast(
            {"type": "status", "state": "connected", "detail": ""}
        )

    async def _bcast_status(self, event: dict[str, Any]) -> None:
        await self._hub.broadcast(
            {
                "type": "status",
                "state": event.get("state", ""),
                "detail": event.get("detail", ""),
            }
        )

    async def _bcast_error(self, event: dict[str, Any]) -> None:
        # "error" = transitorio; el renderer lo pinta con setStatus("error", msg).
        await self._broadcast_problem("error", event.get("message", "error"))

    async def _bcast_fatal(self, event: dict[str, Any]) -> None:
        # "fatal" = irrecuperable (cuota/clave): el renderer lo muestra STICKY.
        await self._broadcast_problem("fatal", event.get("message", "error"))

    async def _bcast_rate_warning(self, event: dict[str, Any]) -> None:
        # Cuota cayendo (ANTES del fatal): aviso informativo para que el operador
        # reaccione. El renderer no conoce "rate_warning" → lo mandamos como error.
        name = event.get("name", "")
        remaining = event.get("remaining", 0)
        limit = event.get("limit", 0)
        message = f"cuota {name} baja: {remaining}/{limit}"
        await self._hub.broadcast({"type": "error", "message": message})

    async def _bcast_response_failed(self, event: dict[str, Any]) -> None:
        # Un turno de Norivy no cerró OK (failed/cancelled/incomplete): informamos
        # sin sellar la sesión (no es fatal, la próxima respuesta puede salir bien).
        status = event.get("status", "?")
        reason = event.get("message", "")
        detail = f"respuesta {status}"
        if reason:
            detail = f"{detail}: {reason}"
        await self._hub.broadcast({"type": "error", "message": detail})

    async def _bcast_function_call(self, event: dict[str, Any]) -> None:
        """El modelo pidió una tool: se ejecuta aparte para no frenar el audio."""
        await self._hub.broadcast(
            {"type": "status", "state": "working", "detail": event["name"]}
        )
        task = asyncio.create_task(self._run_tool(event))
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)

    async def _run_tool(self, event: dict[str, Any]) -> None:
        """Ejecuta la tool y devuelve el resultado al modelo (siempre responde)."""
        assert self._runner is not None
        output = await self._runner.run(event["name"], event["arguments"])
        await self._client.send_function_result(event["call_id"], output)

    async def _broadcast_problem(self, state: str, message: str) -> None:
        """Frame de error + status con el mismo ``state`` (error transitorio/fatal)."""
        await self._hub.broadcast({"type": "error", "message": message})
        await self._hub.broadcast(
            {"type": "status", "state": state, "detail": message}
        )

    async def begin_session(self) -> None:
        """Un cliente tocó la pantalla: abre el mic y saluda (con freno anti-spam)."""
        if not self._greet_limiter.allow():
            logger.info("[session] begin_session ignorado (rate limit)")
            return
        await self.set_mic_muted(False)
        await self._client.create_greeting()

    async def end_session(self) -> None:
        """Fin de la atención (botón o inactividad): deja todo limpio para el siguiente.

        Mutea el mic SIN pedir respuesta (no es un «terminé de hablar»), vacía el
        carrito y descarta la conversación reiniciando el WS de OpenAI.
        """
        self._mic_muted = True
        if self._mic is not None:
            self._mic.set_muted(True)
        await self._client.set_mic_listening(False)
        if self._runner is not None:
            self._runner.reset()
        await self._client.restart()
        await self._hub.broadcast({"type": "session_reset"})
        await self._hub.broadcast({"type": "mic_state", "muted": True})

    async def set_mic_muted(self, muted: bool) -> None:
        """Actualiza mute del mic + listening del cliente + avisa al renderer."""
        self._mic_muted = muted
        if self._mic is not None:
            self._mic.set_muted(muted)
        if muted:
            # Cerrar el mic a mitad de frase = "terminé, respondé con lo que oíste".
            await self._client.commit_and_respond()
        await self._client.set_mic_listening(not muted)
        await self._hub.broadcast({"type": "mic_state", "muted": muted})

    async def stop(self) -> None:
        """Detiene tareas, mic y cliente; deja la sesión inactiva.

        Cierra el cliente SIEMPRE (idempotente) — aunque la sesión no llegara a
        activarse — para no dejar un supervisor de reconexión huérfano.
        """
        was_active = self._active
        self._active = False
        await self._cleanup()
        if was_active:
            logger.info("[session] sesión Realtime detenida")

    async def _cleanup(self) -> None:
        """Cancela las tareas-bridge, cierra el mic y el cliente. Idempotente."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 — cleanup
                logger.warning("[session] task cleanup: %s", exc)
        self._tasks = []
        for tool_task in list(self._tool_tasks):
            tool_task.cancel()
        self._tool_tasks.clear()
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
        await self._client.close()
        if self._runner is not None:
            await self._runner.aclose()


# Tabla de despacho ``kind`` del cliente → handler que broadcastea el/los frame(s)
# del protocolo WS. Agregar un evento = un handler ``_bcast_*`` + una entrada acá.
_EVENT_HANDLERS: dict[
    str, Callable[[RealtimeSession, dict[str, Any]], Awaitable[None]]
] = {
    "audio": RealtimeSession._bcast_audio,
    "transcript": RealtimeSession._bcast_transcript,
    "user_speech_started": RealtimeSession._bcast_user_speech_started,
    "user_transcript": RealtimeSession._bcast_user_transcript,
    "user_transcript_done": RealtimeSession._bcast_user_transcript_done,
    "speech_started": RealtimeSession._bcast_speech_started,
    "speech_done": RealtimeSession._bcast_speech_done,
    "status": RealtimeSession._bcast_status,
    "error": RealtimeSession._bcast_error,
    "fatal": RealtimeSession._bcast_fatal,
    "rate_warning": RealtimeSession._bcast_rate_warning,
    "response_failed": RealtimeSession._bcast_response_failed,
    "function_call": RealtimeSession._bcast_function_call,
}
