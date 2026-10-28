"""TDD B3 — validación de host loopback (evitar exposición accidental).

Comentario en español: el WS local no tiene auth (correcto para loopback). Si
alguien setea NORIVY_KIOSK_HOST=0.0.0.0 en el apuro de un evento, ese WS queda expuesto
a la red del auditorio. Un guard detecta el bind no-loopback para avisar fuerte.
"""

from __future__ import annotations

import importlib

import dotenv
import pytest

from norivy_kiosk import config as config_module
from norivy_kiosk.config import is_loopback_host


@pytest.mark.unit
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "LOCALHOST"])
def test_hosts_loopback(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.unit
@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "10.0.0.1", ""])
def test_hosts_no_loopback(host: str) -> None:
    assert is_loopback_host(host) is False


@pytest.mark.unit
def test_default_model_es_realtime_2_1(monkeypatch: pytest.MonkeyPatch) -> None:
    # config usa load_dotenv(); stubearlo evita que .env.local pise el default.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: False)
    monkeypatch.delenv("NORIVY_KIOSK_MODEL", raising=False)
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.MODEL == "gpt-realtime-2.1"
    finally:
        importlib.reload(config_module)
