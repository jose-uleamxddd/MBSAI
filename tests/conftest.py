"""Fixtures y dobles de prueba compartidos.

Comentario en español: `FakeWebSocket` imita la superficie mínima de un
WebSocket de Starlette que usa el Hub (`send_text`), con opción de fallar para
probar el descarte de clientes muertos.
"""

from __future__ import annotations


class FakeWebSocket:
    """Doble de WebSocket: registra lo enviado; puede simular una caída."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail
        self.closed = False

    async def send_text(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("cliente muerto")
        self.sent.append(text)


import pytest  # noqa: E402

from norivy_kiosk import config  # noqa: E402


@pytest.fixture(autouse=True)
def _store_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credenciales de tienda de mentira: build_runner() no toca la red al armarse."""
    monkeypatch.setattr(config, "STORE_API_URL", "http://tienda.test")
    monkeypatch.setattr(config, "STORE_API_USER", "norivy@tienda.test")
    monkeypatch.setattr(config, "STORE_API_PASSWORD", "secreto")
