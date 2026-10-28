"""Cliente OpenAI Realtime GA (gpt-realtime-2.1) — WebSocket speech-to-speech.

Comentario en español: cliente directo al endpoint Realtime GA de OpenAI. Abre
``wss://api.openai.com/v1/realtime?model=<MODEL>`` con header Authorization
(sin el header beta legacy), manda ``session.update`` con el schema GA (audio
anidado en ``session.audio.input``/``session.audio.output`` + ``server_vad`` por
defecto), y normaliza los eventos del servidor a una asyncio.Queue para la sesión.

Schema GA verificado contra la doc oficial:
- Conexión: query ``model``; header ``Authorization: Bearer ...`` (beta removido).
- ``session.update`` → ``session.type="realtime"``, ``output_modalities``,
  ``instructions``, ``audio.input.format``/``audio.output.format`` con
  ``{"type":"audio/pcm","rate":24000}``, ``audio.output.voice``/``speed``,
  ``audio.input.turn_detection`` = ``server_vad`` (con ``threshold``, default) o
  ``semantic_vad`` según ``config.VAD_MODE``, y ``audio.input.noise_reduction`` =
  ``{"type":"near_field"}`` (default; se OMITE cuando está en ``"off"``).
- Mic: ``input_audio_buffer.append`` con campo ``audio`` (base64 PCM16).
- Turno on-demand: ``response.create`` con ``response.instructions``.
- Eventos servidor: ``response.created``, ``response.output_audio.delta``,
  ``response.output_audio_transcript.delta``, ``response.done``,
  ``input_audio_buffer.speech_started`` / ``.speech_stopped``,
  ``conversation.item.input_audio_transcription.delta`` / ``.completed``
  (transcripción del mic del público), ``error``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

from .. import config, persona
from ..tools import TOOLS

logger = logging.getLogger(__name__)

# Un connector abre el WS de OpenAI. Inyectable para testear reconexión sin red.
Connector = Callable[..., Awaitable[Any]]

_AUDIO_FORMAT_PCM16 = {"type": "audio/pcm", "rate": config.MIC_SAMPLE_RATE}

# PCM16 = 2 bytes por muestra: ms = (bytes / 2 / sample_rate) * 1000. Se usa para
# calcular cuánto audio se reprodujo antes de un barge-in (conversation.item.truncate).
_PCM16_BYTES_PER_SAMPLE = 2
_MS_PER_SECOND = 1000

# Cada cuántos descartes de cola loguear un warning (evita spamear el log).
_DROP_LOG_EVERY = 50

# Evento GA que dispara el barge-in (el público empieza a hablar). El truncate
# de la respuesta en curso es un side-effect async, aparte del evento normalizado.
_SPEECH_STARTED_TYPE = "input_audio_buffer.speech_started"

# Estados de ``response.done.response.status`` que NO son un cierre exitoso: se
# publica un evento extra ``response_failed`` con la razón (observabilidad).
_RESPONSE_DONE_TYPE = "response.done"
_RESPONSE_FAILURE_STATUSES = frozenset({"failed", "cancelled", "incomplete"})

# Errores de OpenAI donde reintentar es INÚTIL (misma key/cuota mala): en vez de
# reconectar en loop, se emite un evento "fatal" persistente para el operador.
_FATAL_ERROR_CODES = frozenset(
    {"invalid_api_key", "insufficient_quota", "account_deactivated"}
)


def _turn_detection(*, create_response: bool) -> dict[str, Any]:
    """Detector de voz GA — ``create_response`` se apaga si el mic está muteado.

    Según ``config.VAD_MODE``: ``server_vad`` (default) usa un ``threshold`` de
    sensibilidad que rechaza audio de fondo (TikTok, videos, bulla) — ideal para el
    mic de la pantalla frente al cliente; ``semantic_vad`` (el
    anterior) detecta cualquier voz por significado (``eagerness``, para salas
    silenciosas). En AMBOS ``interrupt_response: False`` (half-duplex): una vez que
    Norivy ARRANCA una respuesta, NADA la interrumpe. Con parlantes abiertos, dejar
    que el input interrumpa hacía que tu propia voz (la que activó el turno y sigue
    llegando un instante en el borde de arranque) le cortara la respuesta apenas
    empezaba. El mic ya se gatea mientras habla (``session._should_gate_mic``); esto
    sella el borde.
    """
    if config.VAD_MODE == "server_vad":
        return {
            "type": "server_vad",
            "threshold": config.VAD_THRESHOLD,
            "prefix_padding_ms": config.VAD_PREFIX_PADDING_MS,
            "silence_duration_ms": config.VAD_SILENCE_DURATION_MS,
            "create_response": create_response,
            "interrupt_response": False,
        }
    return {
        "type": "semantic_vad",
        "eagerness": config.VAD_EAGERNESS,
        "create_response": create_response,
        "interrupt_response": False,
    }


def _noise_reduction() -> dict[str, str] | None:
    """Config de ``noise_reduction`` GA, o ``None`` para OMITIR el campo.

    OpenAI apaga la reducción de ruido OMITIENDO el campo entero (no mandando un
    valor "off"), así que devolvemos ``None`` cuando ``config.NOISE_REDUCTION`` es
    ``"off"`` para que ``_send_session_update`` no lo incluya.
    """
    if config.NOISE_REDUCTION == "off":
        return None
    return {"type": config.NOISE_REDUCTION}


async def _default_connector(
    url: str,
    *,
    headers: list[tuple[str, str]],
    ping_interval: float,
    ping_timeout: float,
) -> Any:
    """Connector de producción: abre el WS GA de OpenAI con keepalive activo."""
    try:
        from websockets.asyncio.client import connect as ws_connect
    except ImportError as exc:  # pragma: no cover - dependencia externa
        raise RuntimeError(
            "Falta `websockets>=12`. Instalá: pip install 'websockets>=12'"
        ) from exc
    # websockets 13+ usa `additional_headers`; versiones viejas `extra_headers`.
    try:
        return await ws_connect(
            url,
            additional_headers=headers,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )
    except TypeError:
        return await ws_connect(
            url,
            extra_headers=headers,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )


def _audio_b64_to_ms(b64: str) -> float:
    """Duración en ms de un chunk PCM16 base64 (para el truncate de barge-in).

    No decodifica el audio: solo mide el tamaño en bytes desde el largo base64
    (cada 4 chars base64 = 3 bytes, menos el padding ``=``).
    """
    padding = b64.count("=")
    num_bytes = (len(b64) * 3) // 4 - padding
    samples = num_bytes / _PCM16_BYTES_PER_SAMPLE
    return (samples / config.MIC_SAMPLE_RATE) * _MS_PER_SECOND


def _failure_reason(details: Any) -> str:
    """Extrae un texto de razón de ``status_details`` de un response fallido.

    Prioriza ``error.message`` (el caso ``failed``), luego ``reason`` (el caso
    ``incomplete``); si nada de eso está, serializa el dict para no perder pistas.
    """
    if not isinstance(details, dict):
        return ""
    error = details.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    reason = details.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    return json.dumps(details, ensure_ascii=False)


def _backoff_delay(attempt: int, min_s: float, max_s: float) -> float:
    """Backoff exponencial con jitter ±25% (evita reconexiones sincronizadas).

    El exponente se capea (2**30 ya excede cualquier ``max_s`` razonable) para
    NO lanzar OverflowError en un outage largo donde ``attempt`` crece sin techo.
    """
    capped_attempt = min(attempt, 30)
    base = min(min_s * (2.0**capped_attempt), max_s)
    jitter = base * 0.25
    return base + random.uniform(-jitter, jitter)  # jitter no-criptográfico


class OpenAIRealtimeClient:
    """Cliente async del WebSocket Realtime GA; normaliza eventos a una Queue."""

    def __init__(
        self,
        *,
        connector: Connector | None = None,
        reconnect_min_s: float | None = None,
        reconnect_max_s: float | None = None,
    ) -> None:
        self._ws: Any = None
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=config.EVENTS_QUEUE_MAX
        )
        self._dropped_events = 0
        self._supervisor_task: asyncio.Task[None] | None = None
        self._closed = False
        self._fatal = False
        self._listening = False
        self._response_active = False
        # call_id → name de los function_call abiertos (el .done puede no traer name).
        self._pending_calls: dict[str, str] = {}
        # Hay habla del usuario capturada sin responder aún: se usa para el commit
        # manual al cerrar el mic (cerrar = "terminé, respondé con lo que escuchaste").
        self._user_turn_active = False
        # Barge-in: item_id de la respuesta de audio en curso + ms ya reproducidos.
        # Se usan para el conversation.item.truncate cuando el público interrumpe.
        self._active_item_id: str | None = None
        self._audio_ms_played = 0.0
        self._connector: Connector = connector or _default_connector
        self._reconnect_min = (
            reconnect_min_s if reconnect_min_s is not None else config.RECONNECT_MIN_S
        )
        self._reconnect_max = (
            reconnect_max_s if reconnect_max_s is not None else config.RECONNECT_MAX_S
        )

    @property
    def events(self) -> asyncio.Queue[dict[str, Any]]:
        return self._events

    @property
    def dropped_events(self) -> int:
        """Cuántos eventos se descartaron por saturación de la cola (observabilidad)."""
        return self._dropped_events

    @property
    def connected(self) -> bool:
        """True si el WS a OpenAI está abierto (observabilidad para el operador)."""
        return self._ws is not None

    @property
    def is_speaking(self) -> bool:
        """True mientras hay una respuesta de audio de Norivy en curso.

        Lo usa el half-duplex de la sesión para gatear el mic — así el mic no
        captura su propia voz por los parlantes (feedback → bucle de auto-respuesta).
        """
        return self._response_active

    async def connect(self) -> None:
        """Abre el WS GA (con timeout) y lanza el supervisor de reconexión."""
        if self._supervisor_task is not None:
            return
        logger.info("[realtime] conectando → %s (voice=%s)", config.MODEL, config.VOICE)
        await self._emit_status("connecting", config.MODEL)
        try:
            await asyncio.wait_for(self._open(), timeout=config.CONNECT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — boundary de red
            logger.error("[realtime] no se pudo conectar: %s", exc)
            await self._emit_error(f"conexión fallida: {exc}")
            raise
        self._supervisor_task = asyncio.create_task(self._supervise())

    async def _open(self) -> None:
        """Abre UNA conexión WS y manda el ``session.update`` inicial.

        Resetea el estado de respuesta: una respuesta de la conexión vieja ya no
        existe en el socket nuevo (evita un ``response.cancel`` espurio si el
        operador presiona H justo después de reconectar).
        """
        self._response_active = False
        self._pending_calls.clear()
        self._active_item_id = None
        self._audio_ms_played = 0.0
        api_key = config.require_api_key()
        url = f"{config.REALTIME_URL}?model={config.MODEL}"
        headers = [("Authorization", f"Bearer {api_key}")]
        ws = await self._connector(
            url,
            headers=headers,
            ping_interval=config.PING_INTERVAL_S,
            ping_timeout=config.PING_TIMEOUT_S,
        )
        if self._closed:
            # Se pidió cerrar mientras abríamos: no dejar el socket huérfano.
            with contextlib.suppress(Exception):
                await ws.close()
            raise RuntimeError("cliente cerrado durante _open()")
        self._ws = ws
        await self._send_session_update(create_response=self._listening)
        await self._emit_status("connected", config.MODEL)

    async def _supervise(self) -> None:
        """Corre el receive loop; si el WS cae, reconecta con backoff."""
        attempt = 0
        while not self._closed:
            try:
                await self._receive_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — boundary de red
                logger.warning("[realtime] receive loop cortado: %s", exc)
            if self._closed or self._fatal:
                break
            self._ws = None
            # Si el WS cayó a mitad de una respuesta, sellar el estado "hablando":
            # el response.done real nunca llegará (murió con la conexión) y el
            # renderer quedaría con la burbuja "en vivo" colgada para siempre.
            if self._response_active:
                self._response_active = False
                await self._publish({"kind": "speech_done"})
            delay = _backoff_delay(attempt, self._reconnect_min, self._reconnect_max)
            logger.warning(
                "[realtime] WS caído — reconectando en %.1fs (intento %d)",
                delay,
                attempt + 1,
            )
            await self._emit_status("reconnecting", f"intento {attempt + 1}")
            await asyncio.sleep(delay)
            if self._closed:
                break
            try:
                await self._open()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — boundary de red
                logger.warning("[realtime] reconexión falló: %s", exc)
                attempt += 1

    async def _send_session_update(self, *, create_response: bool) -> None:
        """Manda la config GA con audio anidado, voz, speed y el detector de voz.

        El ``noise_reduction`` se agrega condicionalmente: OpenAI lo apaga
        OMITIENDO el campo (ver ``_noise_reduction``), así que solo lo incluimos
        cuando ``config.NOISE_REDUCTION`` no es ``"off"``.
        """
        audio_input: dict[str, Any] = {
            "format": _AUDIO_FORMAT_PCM16,
            "transcription": {"model": config.INPUT_TRANSCRIBE_MODEL},
            "turn_detection": _turn_detection(create_response=create_response),
        }
        noise_reduction = _noise_reduction()
        if noise_reduction is not None:
            audio_input["noise_reduction"] = noise_reduction
        await self._send_json(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": config.SYSTEM_PROMPT,
                    "tools": TOOLS,
                    "tool_choice": "auto",
                    "audio": {
                        "input": audio_input,
                        "output": {
                            "format": _AUDIO_FORMAT_PCM16,
                            "voice": config.VOICE,
                            "speed": config.OUTPUT_SPEED,
                        },
                    },
                },
            }
        )

    async def set_mic_listening(self, listening: bool) -> None:
        """Activa/desactiva el auto-create_response según el mic (tecla M)."""
        self._listening = listening
        if self._ws is None:
            return
        await self._send_json_safe(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "audio": {
                        "input": {
                            "turn_detection": _turn_detection(
                                create_response=listening
                            ),
                        },
                    },
                },
            }
        )
        logger.info("[realtime] mic listening=%s", listening)

    async def commit_and_respond(self) -> None:
        """Cierra el turno del usuario a mano: commitea el buffer de input + dispara
        la respuesta. Se usa al cerrar el mic a mitad de frase para que Norivy responda
        con lo que alcanzó a escuchar, sin esperar el silencio del semantic_vad.

        No-op si no hubo habla del usuario (commitear un buffer vacío daría
        ``input_audio_buffer_commit_empty``) o si Norivy YA está respondiendo (no
        disparar una segunda respuesta encima de la suya)."""
        if self._ws is None or not self._user_turn_active:
            return
        if self._response_active:
            return
        self._user_turn_active = False
        await self._send_json_safe({"type": "input_audio_buffer.commit"})
        await self._send_json_safe({"type": "response.create"})

    async def append_audio(self, pcm16: bytes) -> None:
        """Manda un chunk PCM16 al input buffer (base64)."""
        if not pcm16 or self._ws is None:
            return
        await self._send_json_safe(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16).decode("ascii"),
            }
        )

    async def create_greeting(self) -> None:
        """Dispara el saludo inicial vía ``response.create`` con instrucción propia."""
        if self._ws is None:
            return
        # Si hay una respuesta en curso, la cancelamos: el saludo siempre se dice.
        if self._response_active:
            await self._send_json_safe({"type": "response.cancel"})
        await self._send_json_safe(
            {
                "type": "response.create",
                "response": {
                    "instructions": persona.build_greeting(
                        config.CHARACTER_NAME, config.STORE_NAME
                    )
                },
            }
        )
        logger.info("[realtime] greeting solicitado")

    async def send_function_result(self, call_id: str, output: str) -> None:
        """Devuelve el resultado de una tool y pide que Norivy lo comunique.

        ``function_call_output`` + ``response.create``. Si el primer frame no se
        pudo enviar (WS caído) NO se manda el ``response.create`` — evita una
        respuesta huérfana sin el resultado en la conversación.
        """
        sent = await self._send_json_safe(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )
        if sent:
            await self._send_json_safe({"type": "response.create"})

    async def restart(self) -> None:
        """Descarta la conversación: cierra el WS y deja que el supervisor reabra.

        Es la forma más simple y segura de borrar el contexto entre clientes (no
        queda nada de lo que dijo el anterior). Sin WS abierto es un no-op: la
        reconexión en curso ya arranca una conversación limpia.
        """
        ws = self._ws
        if ws is None:
            return
        self._pending_calls.clear()
        with contextlib.suppress(Exception):
            await ws.close()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("WS Realtime no abierto — llamá connect() primero")
        await self._ws.send(json.dumps(payload))

    async def _send_json_safe(self, payload: dict[str, Any]) -> bool:
        """Manda un frame degradando LIMPIO si el WS murió a mitad del envío.

        Para acciones del operador (``greet``/``mic``/audio): si el socket cae en
        pleno ``send`` (no antes, cuando ``_ws is None``), NO se propaga — el
        supervisor reconecta. Log DEBUG, no WARNING: es esperado en un blip de
        red. ``_send_json`` crudo se reserva para ``_open`` (ahí el fallo DEBE
        propagar para disparar la reconexión). Devuelve ``True`` si se envió.
        """
        if self._ws is None:
            return False
        try:
            await self._send_json(payload)
            return True
        except (ConnectionError, OSError, RuntimeError) as exc:
            logger.debug("[realtime] send descartado (WS cayó a mitad): %s", exc)
            return False

    async def _receive_loop(self) -> None:
        """Lee del WS hasta que cierra; publica eventos normalizados.

        Al cerrarse el WS el ``async for`` termina y esta corrutina retorna: el
        supervisor (``_supervise``) es quien decide si reconectar.
        """
        ws = self._ws
        if ws is None:
            return
        async for raw in ws:
            if self._closed:
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[realtime] frame no-JSON ignorado")
                continue
            event = self._normalize(msg)
            if event is not None:
                await self._publish(event)
            await self._handle_side_effects(msg)
            if self._fatal:
                return  # error fatal de OpenAI: cortar, el supervisor no reconecta

    async def _handle_side_effects(self, msg: dict[str, Any]) -> None:
        """Side-effects async que van MÁS ALLÁ del evento normalizado del msg.

        Dos casos: (1) barge-in → truncar la respuesta al interrumpir; (2)
        ``response.done`` con status no-exitoso → publicar la razón del fallo.
        Se mantienen fuera de ``_normalize`` (puro, un solo evento) a propósito.
        """
        msg_type = msg.get("type")
        if msg_type == _SPEECH_STARTED_TYPE:
            await self._on_user_speech_started_async(msg)
        elif msg_type == _RESPONSE_DONE_TYPE:
            failure = self._response_failure_event(msg)
            if failure is not None:
                await self._publish(failure)

    def _normalize(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Mapea el evento GA del servidor al evento normalizado interno.

        Los tipos exactos se despachan por tabla (``_EXACT_EVENT_HANDLERS``); los
        de sufijo (transcripción del público) y el error se resuelven aparte.
        Agregar un evento GA nuevo = un handler ``_on_*`` + una entrada en la tabla.
        """
        msg_type = msg.get("type", "")
        handler = _EXACT_EVENT_HANDLERS.get(msg_type)
        if handler is not None:
            return handler(self, msg)
        if msg_type.endswith("input_audio_transcription.delta"):
            return {"kind": "user_transcript", "delta": msg.get("delta", "")}
        if msg_type.endswith("input_audio_transcription.completed"):
            return {
                "kind": "user_transcript_done",
                "transcript": msg.get("transcript", ""),
            }
        if msg_type == "error":
            return self._normalize_error(msg.get("error", {}))
        logger.debug("[realtime] evento sin mapear: %s", msg_type)
        return None

    def _normalize_error(self, err: dict[str, Any]) -> dict[str, Any]:
        """Distingue error FATAL (cuota/clave, reintentar es inútil) de transitorio."""
        message = err.get("message", str(err))
        code = err.get("code") or err.get("type") or ""
        if code in _FATAL_ERROR_CODES:
            self._fatal = True
            return {"kind": "fatal", "message": message, "code": code}
        return {"kind": "error", "message": message}

    def _on_response_created(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        self._response_active = True
        # El turno del usuario ya se está respondiendo: cerrarlo para que un cierre
        # de mic posterior no dispare un commit encima de la respuesta en curso.
        self._user_turn_active = False
        # Nueva respuesta: reset del tracking de barge-in del turno anterior.
        self._active_item_id = None
        self._audio_ms_played = 0.0
        return {"kind": "speech_started"}

    def _on_output_item_added(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        # Capturamos el item_id de la respuesta en curso: se necesita para el
        # conversation.item.truncate si el público interrumpe (barge-in).
        item = msg.get("item")
        if isinstance(item, dict):
            item_id = item.get("id")
            if isinstance(item_id, str):
                self._active_item_id = item_id
            if item.get("type") == "function_call":
                self._pending_calls[str(item.get("call_id", ""))] = str(
                    item.get("name", "")
                )
        return None

    def _on_function_call_done(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Argumentos de una tool completos → evento para que la sesión la ejecute."""
        call_id = str(msg.get("call_id", ""))
        name = str(msg.get("name") or self._pending_calls.pop(call_id, ""))
        self._pending_calls.pop(call_id, None)
        if not call_id or not name:
            return None
        return {
            "kind": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": str(msg.get("arguments", "")),
        }

    def _on_audio_delta(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        delta = msg.get("delta", "")
        if not delta:
            return None
        self._audio_ms_played += _audio_b64_to_ms(delta)
        return {"kind": "audio", "pcm16_b64": delta}

    def _on_transcript_delta(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        return {"kind": "transcript", "delta": msg.get("delta", "")}

    def _on_response_done(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        self._response_active = False
        return {"kind": "speech_done"}

    @staticmethod
    def _response_failure_event(msg: dict[str, Any]) -> dict[str, Any] | None:
        """Evento ``response_failed`` si el ``status`` no fue un cierre exitoso.

        Devuelve ``None`` en el caso feliz (``completed``): una respuesta OK no
        genera ruido. La razón sale de ``status_details`` (``error.message`` o
        ``reason``), con fallback al dict entero para no perder contexto.
        """
        response = msg.get("response")
        if not isinstance(response, dict):
            return None
        status = response.get("status")
        if status not in _RESPONSE_FAILURE_STATUSES:
            return None
        details = response.get("status_details")
        # Barge-in: una respuesta cancelada porque el cliente empezó a hablar
        # (``turn_detected``) NO es un fallo — es el comportamiento ESPERADO del
        # barge-in. No la reportes como error (si no, el operador ve un banner
        # rojo "respuesta cancelled: turn_detected" en CADA interrupción del
        # cliente). Otros cancelled/failed/incomplete sí se reportan.
        if (
            status == "cancelled"
            and isinstance(details, dict)
            and details.get("reason") == "turn_detected"
        ):
            return None
        return {
            "kind": "response_failed",
            "status": status,
            "message": _failure_reason(details),
        }

    def _on_user_speech_started(
        self, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        # El público empezó a hablar: hay un turno del usuario en curso (habilita el
        # commit manual si el operador cierra el mic a mitad de frase).
        self._user_turn_active = True
        # Abrimos su burbuja YA (queda arriba de la respuesta de Norivy aunque la
        # transcripción llegue tarde).
        return {"kind": "user_speech_started"}

    async def _on_user_speech_started_async(self, msg: dict[str, Any]) -> None:
        """Barge-in: si hay respuesta activa, truncarla al punto ya reproducido.

        OpenAI cancela la respuesta al detectar habla del público, pero el modelo
        cree que se escuchó TODO el audio: hay que decirle cuánto sonó de verdad
        (``conversation.item.truncate``) para no desalinear el contexto. Degrada
        en silencio si el WS cayó (``_ws is None``) — el supervisor reconecta.
        """
        if not self._response_active or self._active_item_id is None:
            return
        if self._ws is None:
            return
        await self._send_json_safe(
            {
                "type": "conversation.item.truncate",
                "item_id": self._active_item_id,
                "content_index": 0,
                "audio_end_ms": int(self._audio_ms_played),
            }
        )

    def _on_user_speech_stopped(
        self, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        return {"kind": "status", "state": "connected", "detail": ""}

    def _on_rate_limits(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Avisa si alguna cuota cae bajo el umbral, ANTES del fatal por agotarla.

        OpenAI manda ``rate_limits.updated`` con la fracción restante de cada
        límite; si baja de ``RATE_LIMIT_WARN_REMAINING`` se emite un aviso para
        que el operador reaccione antes de que Norivy quede muda de golpe.
        """
        limits = msg.get("rate_limits")
        if not isinstance(limits, list):
            return None
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            limit = entry.get("limit", 0)
            remaining = entry.get("remaining", 0)
            if not isinstance(limit, (int, float)) or limit <= 0:
                continue
            if remaining / limit < config.RATE_LIMIT_WARN_REMAINING:
                return {
                    "kind": "rate_warning",
                    "name": entry.get("name", ""),
                    "remaining": remaining,
                    "limit": limit,
                }
        return None

    async def _publish(self, event: dict[str, Any]) -> None:
        """Encola un evento; si la cola está llena, descarta el más viejo.

        El descarte se CONTABILIZA (``dropped_events``) y se loguea cada cierto
        número: perder audio/control en silencio es deuda de resiliencia invisible.
        """
        if self._events.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
                self._dropped_events += 1
                if self._dropped_events % _DROP_LOG_EVERY == 1:
                    logger.warning(
                        "[realtime] cola de eventos saturada: %d descartados",
                        self._dropped_events,
                    )
        await self._events.put(event)

    async def _emit_status(self, state: str, detail: str) -> None:
        await self._publish({"kind": "status", "state": state, "detail": detail})

    async def _emit_error(self, message: str) -> None:
        await self._publish({"kind": "error", "message": message})

    async def close(self) -> None:
        """Cierra el supervisor y el WebSocket de forma segura."""
        self._closed = True
        if self._supervisor_task is not None and not self._supervisor_task.done():
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 — cleanup
                logger.warning("[realtime] supervisor cleanup: %s", exc)
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:  # noqa: BLE001 — cleanup
                logger.warning("[realtime] ws.close falló: %s", exc)
        self._ws = None


# Tabla de despacho por tipo exacto de evento GA → handler. Los tipos de sufijo
# (transcripción) y el error se resuelven fuera de la tabla en `_normalize`.
_EXACT_EVENT_HANDLERS: dict[
    str, Callable[[OpenAIRealtimeClient, dict[str, Any]], dict[str, Any] | None]
] = {
    "response.created": OpenAIRealtimeClient._on_response_created,
    "response.output_item.added": OpenAIRealtimeClient._on_output_item_added,
    "response.function_call_arguments.done": (
        OpenAIRealtimeClient._on_function_call_done
    ),
    "response.output_audio.delta": OpenAIRealtimeClient._on_audio_delta,
    "response.output_audio_transcript.delta": (
        OpenAIRealtimeClient._on_transcript_delta
    ),
    "response.done": OpenAIRealtimeClient._on_response_done,
    "response.cancelled": OpenAIRealtimeClient._on_response_done,
    "input_audio_buffer.speech_started": (
        OpenAIRealtimeClient._on_user_speech_started
    ),
    "input_audio_buffer.speech_stopped": (
        OpenAIRealtimeClient._on_user_speech_stopped
    ),
    "rate_limits.updated": OpenAIRealtimeClient._on_rate_limits,
}
