"""TDD FIX 2 — observar ``rate_limits.updated`` y avisar antes del fatal.

Comentario en español: OpenAI manda ``rate_limits.updated`` con la cuota
restante. Si baja de ``config.RATE_LIMIT_WARN_REMAINING`` hay que avisar al
operador ANTES de que salte el error fatal ``insufficient_quota`` (que deja a
Norivy muda de golpe). Lógica pura sobre ``_normalize`` — sin red.
"""

from __future__ import annotations

import pytest

from norivy_kiosk import config
from norivy_kiosk.realtime.client import OpenAIRealtimeClient


@pytest.mark.unit
def test_rate_limits_bajos_publican_aviso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: umbral 0.15; remaining/limit = 100/1000 = 0.10 < 0.15 → avisar.
    monkeypatch.setattr(config, "RATE_LIMIT_WARN_REMAINING", 0.15)
    client = OpenAIRealtimeClient()

    event = client._normalize(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {"name": "requests", "limit": 500, "remaining": 480},
                {"name": "tokens", "limit": 1000, "remaining": 100},
            ],
        }
    )

    assert event is not None
    assert event["kind"] == "rate_warning"
    assert event["remaining"] == 100
    assert event["limit"] == 1000
    assert event["name"] == "tokens"


@pytest.mark.unit
def test_rate_limits_altos_no_avisan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: toda la cuota holgada (>15%) → ningún aviso.
    monkeypatch.setattr(config, "RATE_LIMIT_WARN_REMAINING", 0.15)
    client = OpenAIRealtimeClient()

    event = client._normalize(
        {
            "type": "rate_limits.updated",
            "rate_limits": [
                {"name": "requests", "limit": 500, "remaining": 450},
                {"name": "tokens", "limit": 1000, "remaining": 900},
            ],
        }
    )

    assert event is None


@pytest.mark.unit
def test_rate_limits_limit_cero_no_crashea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: un limit=0 no debe dividir por cero; se ignora esa entrada.
    monkeypatch.setattr(config, "RATE_LIMIT_WARN_REMAINING", 0.15)
    client = OpenAIRealtimeClient()

    event = client._normalize(
        {
            "type": "rate_limits.updated",
            "rate_limits": [{"name": "weird", "limit": 0, "remaining": 0}],
        }
    )

    assert event is None
