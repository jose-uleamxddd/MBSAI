"""TDD B3 — rate limiter simple para frenar el spam de `greet` (DoS económico).

Comentario en español: cada `greet` dispara un response.create facturado en
OpenAI. Sin freno, un proceso local podría spamearlo. El limiter usa un clock
inyectable para testear sin dormir.
"""

from __future__ import annotations

import pytest

from norivy_kiosk.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.mark.unit
def test_permite_el_primero_y_bloquea_dentro_del_intervalo() -> None:
    clock = FakeClock()
    limiter = RateLimiter(min_interval_s=3.0, clock=clock)

    assert limiter.allow() is True  # el primero siempre pasa
    assert limiter.allow() is False  # inmediato después: bloqueado


@pytest.mark.unit
def test_permite_de_nuevo_pasado_el_intervalo() -> None:
    clock = FakeClock()
    limiter = RateLimiter(min_interval_s=3.0, clock=clock)

    assert limiter.allow() is True
    clock.advance(3.0)
    assert limiter.allow() is True  # ya pasó el intervalo
