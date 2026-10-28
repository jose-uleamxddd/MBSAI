"""TDD — app.py: /healthz + WS /ws/kiosk + lifespan (arranque normal y degradado).

Comentario en español: cubre el server HTTP+WS de punta a punta con el TestClient
de Starlette (corre el lifespan REAL), inyectando una sesión FAKE vía el
``session_factory`` de ``create_app`` — nunca se toca OpenAI ni el mic real. Los
paths que interesan: el lifespan (arranque OK y arranque DEGRADADO cuando
``session.start()`` explota → el server sigue vivo para /healthz), el WS handler
(``hello`` → ``start``/``reset`` vía ``_dispatch`` → cleanup) y ``/healthz``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from norivy_kiosk import config
from norivy_kiosk.app import create_app
from norivy_kiosk.hub import Hub


class _FakeSession:
    """Doble de RealtimeSession: imita SOLO la superficie que consume app.py.

    Registra las llamadas de control (start/reset) para asertar el ruteo del
    ``_dispatch``, y expone las properties que lee ``/healthz``/``hello``. Puede
    scriptearse para fallar en ``start()`` (arranque degradado) y para reportar
    distintos estados de conexión OpenAI.
    """

    def __init__(
        self,
        hub: Hub,
        *,
        fail_start: bool = False,
        fail_hello: bool = False,
        connected: bool = True,
        mic_alive: bool = True,
    ) -> None:
        self._hub = hub
        self._fail_start = fail_start
        self._fail_hello = fail_hello
        self._connected = connected
        self._mic_alive = mic_alive
        self._started = False
        self._muted = True
        self.start_calls = 0
        self.stop_calls = 0
        self.begin_calls = 0
        self.reset_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        if self._fail_start:
            raise RuntimeError("no se pudo arrancar la sesión Realtime")
        self._started = True

    async def stop(self) -> None:
        self.stop_calls += 1
        self._started = False

    def hello(self) -> dict[str, Any]:
        if self._fail_hello:
            # Fuerza un error INESPERADO dentro del WS handler (no un
            # WebSocketDisconnect): debe caer en el except genérico, loguear y
            # limpiar el hub sin tirar el server.
            raise RuntimeError("hello reventó (error interno inesperado)")
        return {
            "type": "hello",
            "character_name": config.CHARACTER_NAME,
            "store_name": config.STORE_NAME,
            "model": config.MODEL,
            "voice": config.VOICE,
            "mic_muted": self.mic_muted,
            "realtime_active": self.realtime_active,
        }

    async def begin_session(self) -> None:
        self.begin_calls += 1

    async def end_session(self) -> None:
        # Como el RealtimeSession real: mutea y avisa al renderer por el Hub
        # (frame mic_state). Sin esto el WS handler no emitiría respuesta.
        self.reset_calls += 1
        self._muted = True
        await self._hub.broadcast({"type": "mic_state", "muted": True})

    @property
    def mic_muted(self) -> bool:
        return self._muted

    @property
    def realtime_active(self) -> bool:
        return self._started and self._connected

    @property
    def openai_connected(self) -> bool:
        return self._connected

    @property
    def mic_alive(self) -> bool:
        return self._started and self._mic_alive


class _Harness:
    """Une un TestClient a la _FakeSession que su lifespan crea.

    La fake necesita el Hub que ``create_app`` fabrica, así que se construye
    DENTRO de la factory (con el hub real) y se expone acá para asertar sus
    contadores (start/stop/start-session/reset).
    """

    def __init__(self, **session_kwargs: Any) -> None:
        self.session: _FakeSession | None = None

        def factory(hub: Hub) -> _FakeSession:
            self.session = _FakeSession(hub, **session_kwargs)
            return self.session

        self.client = TestClient(create_app(session_factory=factory))  # type: ignore[arg-type]

    def require_session(self) -> _FakeSession:
        # El lifespan ya corrió la factory al entrar al `with`.
        assert self.session is not None
        return self.session


class _RecordingWS:
    """WebSocket fake mínimo para invocar el endpoint del WS sin el TestClient.

    Solo implementa la superficie que ``ws_kiosk`` toca antes de que la sesión
    fake reviente en ``hello()``: ``accept`` (y ``send_json``/``receive_json``
    por si el flujo llega más lejos en otros usos).
    """

    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def _find_ws_endpoint(app: Any) -> Any:
    """Devuelve la corrutina del endpoint montado en /ws/kiosk."""
    from starlette.routing import WebSocketRoute

    for route in app.routes:
        if isinstance(route, WebSocketRoute) and route.path == "/ws/kiosk":
            return route.endpoint
    raise AssertionError("endpoint /ws/kiosk no encontrado")


def hub_client_count(hub: Hub) -> int:
    """Cuántos clientes tiene el Hub (lee su set privado para la aserción)."""
    return len(hub._clients)  # noqa: SLF001 — inspección de estado en test


# --------------------------------------------------------------------------- #
# /healthz                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_healthz_ok_shape_con_sesion_activa() -> None:
    harness = _Harness(connected=True)
    with harness.client as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    # El contrato de observabilidad: estas tres claves las lee el operador.
    assert body["realtime_active"] is True
    assert body["openai_connected"] is True
    assert body["mic_alive"] is True
    # Metadata estable expuesta al operador.
    assert body["ok"] is True
    assert body["model"] == config.MODEL
    assert body["voice"] == config.VOICE
    assert body["mic_muted"] is True


@pytest.mark.integration
def test_healthz_realtime_inactivo_si_openai_no_conectado() -> None:
    # Sesión arrancada pero OpenAI caído → realtime_active False, sin 500.
    harness = _Harness(connected=False, mic_alive=True)
    with harness.client as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["realtime_active"] is False
    assert body["openai_connected"] is False
    assert body["mic_alive"] is True


@pytest.mark.integration
def test_healthz_y_hello_coinciden_en_realtime_active_via_ws() -> None:
    # /healthz (operador) y el frame hello (renderer) DEBEN reportar el mismo
    # realtime_active. Acá con OpenAI caído (el caso donde antes divergían).
    harness = _Harness(connected=False)
    with harness.client as client:
        health = client.get("/healthz").json()
        with client.websocket_connect("/ws/kiosk") as ws:
            hello = ws.receive_json()

    assert health["realtime_active"] == hello["realtime_active"]
    assert health["realtime_active"] is False


# --------------------------------------------------------------------------- #
# Lifespan (arranque normal y DEGRADADO)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_lifespan_arranca_y_para_la_sesion() -> None:
    # El lifespan arranca la sesión al entrar y la detiene al salir.
    harness = _Harness(connected=True)
    with harness.client as client:
        client.get("/healthz")

    session = harness.require_session()
    assert session.start_calls == 1
    assert session.stop_calls == 1


@pytest.mark.integration
def test_lifespan_degradado_no_tira_el_server() -> None:
    # session.start() explota → el server ARRANCA igual (resiliencia): /healthz
    # responde 200 con realtime_active False, NO un 500.
    harness = _Harness(fail_start=True, connected=False)
    with harness.client as client:
        resp = client.get("/healthz")

    assert harness.require_session().start_calls == 1  # se intentó arrancar
    assert resp.status_code == 200  # el fallo NO tiró el server
    assert resp.json()["realtime_active"] is False


@pytest.mark.integration
def test_lifespan_degradado_igual_frena_la_sesion_al_salir() -> None:
    # Aún con arranque fallido, stop() se llama al cerrar (cleanup incondicional).
    harness = _Harness(fail_start=True)
    with harness.client as client:
        client.get("/healthz")

    assert harness.require_session().stop_calls == 1


# --------------------------------------------------------------------------- #
# WS /ws/kiosk — hello + dispatch (start / reset) + robustez              #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_ws_manda_hello_al_conectar() -> None:
    harness = _Harness(connected=True)
    with harness.client as client, client.websocket_connect("/ws/kiosk") as ws:
        hello = ws.receive_json()

    assert hello["type"] == "hello"
    assert hello["character_name"] == config.CHARACTER_NAME
    assert hello["realtime_active"] is True


@pytest.mark.integration
def test_ws_start_inicia_la_sesion() -> None:
    harness = _Harness(connected=True)
    with harness.client as client, client.websocket_connect("/ws/kiosk") as ws:
        ws.receive_json()  # hello
        ws.send_json({"type": "start"})
        # Segundo round-trip: el reset produce un mic_state de vuelta,
        # y como el WS server procesa mensajes en orden, recibirlo garantiza
        # que el greet previo YA se despachó antes de cerrar el contexto.
        ws.send_json({"type": "reset"})
        ws.receive_json()  # mic_state

    assert harness.require_session().begin_calls == 1


@pytest.mark.integration
def test_ws_reset_devuelve_mic_state() -> None:
    harness = _Harness(connected=True)
    with harness.client as client, client.websocket_connect("/ws/kiosk") as ws:
        ws.receive_json()  # hello
        ws.send_json({"type": "reset"})
        frame = ws.receive_json()

    assert frame["type"] == "mic_state"
    assert frame["muted"] is True  # reset deja el mic muteado
    assert harness.require_session().reset_calls == 1


@pytest.mark.integration
def test_ws_mensaje_basura_no_crashea() -> None:
    # Un type desconocido cae en el else del _dispatch (log debug) y NO rompe el
    # bridge: el WS sigue vivo y un reset posterior responde normal.
    harness = _Harness(connected=True)
    with harness.client as client, client.websocket_connect("/ws/kiosk") as ws:
        ws.receive_json()  # hello
        ws.send_json({"type": "no-existe-este-type"})
        ws.send_json({"type": "reset"})
        frame = ws.receive_json()

    assert frame["type"] == "mic_state"
    assert harness.require_session().begin_calls == 0  # la basura no disparó nada


@pytest.mark.integration
def test_ws_disconnect_limpia_el_hub_sin_error() -> None:
    # Al cerrar el WS, el finally del handler des-registra del Hub sin propagar.
    # Reconectar tras el disconnect prueba que el cleanup dejó el Hub sano.
    harness = _Harness(connected=True)
    with harness.client as client:
        with client.websocket_connect("/ws/kiosk") as ws:
            ws.receive_json()  # hello
        with client.websocket_connect("/ws/kiosk") as ws2:
            again = ws2.receive_json()

    assert again["type"] == "hello"


@pytest.mark.integration
def test_lifespan_avisa_si_host_no_es_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Guardia de seguridad: con un host NO loopback el WS queda expuesto sin auth,
    # así que el lifespan loguea un warning fuerte. El server DEBE arrancar igual
    # (el warning avisa, no bloquea) y /healthz responde 200.
    monkeypatch.setattr(config, "HOST", "0.0.0.0")  # noqa: S104 — a propósito
    harness = _Harness(connected=True)
    with harness.client as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    assert harness.require_session().start_calls == 1


@pytest.mark.integration
async def test_ws_error_inesperado_se_traga_y_limpia_el_hub() -> None:
    # Si el handler del WS revienta con algo que NO es WebSocketDisconnect (acá
    # hello() lanza), cae en el except genérico: loguea y limpia el hub SIN
    # propagar. Se ejercita el endpoint con un WebSocket FAKE (no el TestClient,
    # que cuelga cuando el WS server-side termina por excepción interna) para
    # cubrir el branch de forma determinística.
    hub = Hub()
    session = _FakeSession(hub, fail_hello=True)

    def factory(_hub: Hub) -> _FakeSession:
        return session

    ws_kiosk = _find_ws_endpoint(create_app(session_factory=factory))  # type: ignore[arg-type]
    fake_ws = _RecordingWS()

    # No debe propagar: el error interno se traga en el except genérico.
    await ws_kiosk(fake_ws)  # type: ignore[arg-type]

    assert fake_ws.accepted is True  # llegó a aceptar antes de reventar
    # El finally corrió: el hub quedó sin clientes colgados (register+unregister).
    assert hub_client_count(hub) == 0


@pytest.mark.integration
def test_default_factory_usa_realtime_session_sin_arrancar() -> None:
    # Sin session_factory, create_app cablea el RealtimeSession real (runtime
    # intacto). No entramos al lifespan (no TestClient) para no tocar OpenAI: solo
    # verificamos que la app se construye y /healthz existe como ruta.
    from starlette.routing import Route

    app = create_app()
    paths = {r.path for r in app.routes if isinstance(r, Route)}
    assert "/healthz" in paths


@pytest.fixture(autouse=True)
def _quiet_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # El lifespan real llama config.require_api_key(); con la sesión fake el
    # arranque NO depende de la key, pero fijamos una dummy para que el path OK
    # del lifespan (require_api_key + start) no loguee un error espurio.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-dummy")
    yield
