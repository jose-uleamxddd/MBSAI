"""Half-duplex: descartar el mic mientras Norivy genera Y hasta que su audio termina
de SONAR — estimado por la DURACIÓN real del audio, no por cuándo OpenAI terminó de
generar (que es mucho antes y dejaba reabrir el mic a mitad de la respuesta → el mic
capta su propia voz por los parlantes → feedback → bucle). Reloj y cliente
inyectados; sin red ni hardware.
"""

from __future__ import annotations

from norivy_kiosk import config
from norivy_kiosk.hub import Hub
from norivy_kiosk.realtime.session import RealtimeSession


class _FakeClient:
    """Solo lo que el half-duplex mira: si Norivy está generando audio."""

    def __init__(self) -> None:
        self.speaking = False

    @property
    def is_speaking(self) -> bool:
        return self.speaking


class _Clock:
    """Reloj controlable para verificar el gate sin dormir."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _session(client: _FakeClient, clock: _Clock) -> RealtimeSession:
    return RealtimeSession(Hub(), client=client, clock=clock)


def test_gatea_el_mic_mientras_norivy_genera() -> None:
    client, clock = _FakeClient(), _Clock()
    sess = _session(client, clock)
    client.speaking = True
    assert sess._should_gate_mic() is True


def test_mic_libre_cuando_no_habla_y_su_audio_ya_termino() -> None:
    client, clock = _FakeClient(), _Clock()
    sess = _session(client, clock)
    client.speaking = False
    clock.t = 100.0
    assert sess._should_gate_mic() is False


def test_gatea_hasta_que_el_audio_estimado_termina_de_sonar() -> None:
    client, clock = _FakeClient(), _Clock()
    sess = _session(client, clock)
    # Norivy dejó de GENERAR pero su audio suena hasta t=5 (lo setea _bcast_audio).
    sess._speaking_tail_until = 5.0
    client.speaking = False
    clock.t = 4.9
    assert sess._should_gate_mic() is True  # su audio aún suena
    clock.t = 5.1
    assert sess._should_gate_mic() is False  # terminó de sonar


async def test_bcast_audio_gatea_por_la_duracion_real_del_audio() -> None:
    client, clock = _FakeClient(), _Clock()
    sess = _session(client, clock)
    clock.t = 10.0
    # ~1s de audio: 24000 samples × 2 bytes = 48000 bytes → base64 ≈ 64000 chars.
    one_second_b64 = "A" * 64000
    await sess._bcast_audio({"pcm16_b64": one_second_b64})

    # OpenAI generó de golpe (ya no habla), pero el audio de ~1s todavía SUENA.
    client.speaking = False
    clock.t = 10.5
    assert sess._should_gate_mic() is True  # el segundo de audio no terminó aún

    clock.t = 10.0 + 1.0 + config.HALF_DUPLEX_TAIL_S + 0.1
    assert sess._should_gate_mic() is False  # audio + margen agotados
