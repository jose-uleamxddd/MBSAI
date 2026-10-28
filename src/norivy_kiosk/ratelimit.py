"""RateLimiter — intervalo mínimo entre acciones (freno anti-spam).

Comentario en español: usado para frenar el spam de `greet` (cada greet dispara
un response.create facturado en OpenAI). Clock inyectable para testear sin dormir.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """Permite una acción como mucho cada ``min_interval_s`` segundos."""

    def __init__(
        self,
        min_interval_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval = min_interval_s
        self._clock = clock
        self._last: float | None = None

    def allow(self) -> bool:
        """True si la acción procede ahora; False si cae dentro del intervalo."""
        now = self._clock()
        if self._last is not None and now - self._last < self._min_interval:
            return False
        self._last = now
        return True
