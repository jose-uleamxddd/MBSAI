"""Cliente de la tienda: login/refresh del token, reintento en 401 y errores."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from norivy_kiosk.store_api import QuoteItem, StoreApiError, StoreClient
from norivy_kiosk.store_api.auth import AuthError, TokenManager

Handler = Callable[[httpx.Request], httpx.Response]


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _http(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://tienda.test", transport=httpx.MockTransport(handler)
    )


def _tokens(http: httpx.AsyncClient, clock: _Clock | None = None) -> TokenManager:
    return TokenManager(
        http, user="u", password="p", skew_s=10.0, clock=clock or _Clock()
    )


def _login_ok(access: str = "A1", expires_in: int = 100) -> httpx.Response:
    return httpx.Response(
        200, json={"accessToken": access, "refreshToken": "R1", "expiresIn": expires_in}
    )


async def test_get_hace_login_una_sola_vez_y_cachea() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return _login_ok()

    async with _http(handler) as http:
        tokens = _tokens(http)
        assert await tokens.get() == "A1"
        assert await tokens.get() == "A1"

    assert calls == ["/auth/login"]


async def test_get_concurrente_hace_un_solo_login() -> None:
    calls = 0

    async def slow_handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return _login_ok()

    async with _http(slow_handler) as http:  # type: ignore[arg-type]
        tokens = _tokens(http)
        results = await asyncio.gather(*(tokens.get() for _ in range(5)))

    assert results == ["A1"] * 5
    assert calls == 1


async def test_renueva_con_refresh_token_al_vencer() -> None:
    clock = _Clock()
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        if req.url.path == "/auth/refresh":
            assert json.loads(req.content) == {"refreshToken": "R1"}
            return _login_ok("A2")
        return _login_ok("A1")

    async with _http(handler) as http:
        tokens = _tokens(http, clock)
        assert await tokens.get() == "A1"
        clock.t = 95.0  # expiresIn 100 - skew 10 → ya venció
        assert await tokens.get() == "A2"

    assert paths == ["/auth/login", "/auth/refresh"]


async def test_si_el_refresh_falla_cae_a_login() -> None:
    clock = _Clock()
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        if req.url.path == "/auth/refresh":
            return httpx.Response(401)
        return _login_ok("A3")

    async with _http(handler) as http:
        tokens = _tokens(http, clock)
        await tokens.get()
        clock.t = 500.0
        assert await tokens.get() == "A3"

    assert paths == ["/auth/login", "/auth/refresh", "/auth/login"]


async def test_login_con_credenciales_malas_levanta_auth_error() -> None:
    async with _http(lambda req: httpx.Response(401)) as http:
        with pytest.raises(AuthError):
            await _tokens(http).get()


def _client(handler: Handler) -> StoreClient:
    http = _http(handler)
    return StoreClient(http, _tokens(http))


async def test_401_invalida_el_token_y_reintenta_una_vez() -> None:
    seen_auth: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/auth/login":
            return _login_ok("A1")
        if req.url.path == "/auth/refresh":  # tras el 401 se renueva con el refresh
            return _login_ok("A2")
        seen_auth.append(req.headers["authorization"])
        if req.headers["authorization"] == "Bearer A1":
            return httpx.Response(401)
        return httpx.Response(200, json={"items": []})

    client = _client(handler)
    assert await client.search_products(text="pastillas") == []
    assert seen_auth == ["Bearer A1", "Bearer A2"]
    await client.aclose()


async def test_401_persistente_levanta_store_api_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/auth/login":
            return _login_ok()
        return httpx.Response(401)

    client = _client(handler)
    with pytest.raises(StoreApiError):
        await client.search_products(text="x")
    await client.aclose()


async def test_error_de_red_se_traduce_a_store_api_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    client = _client(handler)
    with pytest.raises(StoreApiError, match="no responde|conectarme"):
        await client.search_products(text="x")
    await client.aclose()


async def test_search_omite_filtros_vacios_y_parsea_productos() -> None:
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/auth/login":
            return _login_ok()
        captured.update(dict(req.url.params))
        return httpx.Response(
            200,
            json=[
                {
                    "id": 7,
                    "name": "Filtro",
                    "price": "6.9",
                    "type": "repuesto",
                    "stock": 3,
                }
            ],
        )

    client = _client(handler)
    products = await client.search_products(text="filtro", model="CB 190R", year=2020)

    assert captured == {"q": "filtro", "model": "CB 190R", "year": "2020", "limit": "6"}
    assert products[0].id == "7"
    assert products[0].price == pytest.approx(6.9)
    assert products[0].stock == 3
    await client.aclose()


async def test_create_quote_vuelve_absoluta_la_url_del_pdf() -> None:
    body: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/auth/login":
            return _login_ok()
        body.update(json.loads(req.content))
        return httpx.Response(
            200, json={"id": "C1", "total": 25.4, "pdfUrl": "/quotes/C1.pdf"}
        )

    client = _client(handler)
    quote = await client.create_quote([QuoteItem("r1", 2)], customer_name="Ana")

    assert body == {
        "items": [{"productId": "r1", "quantity": 2}],
        "customerName": "Ana",
    }
    assert quote.pdf_url == "http://tienda.test/quotes/C1.pdf"
    assert quote.total == pytest.approx(25.4)
    await client.aclose()


async def test_create_quote_sin_enlace_de_descarga_falla() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/auth/login":
            return _login_ok()
        return httpx.Response(200, json={"id": "C1", "total": 1})

    client = _client(handler)
    with pytest.raises(StoreApiError, match="enlace"):
        await client.create_quote([QuoteItem("r1", 1)])
    await client.aclose()
