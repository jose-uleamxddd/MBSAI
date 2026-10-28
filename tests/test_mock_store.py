"""StoreClient contra la API de mentira (login, expiración, catálogo, cotización)."""

from __future__ import annotations

import httpx
import pytest

from norivy_kiosk import mock_store
from norivy_kiosk.store_api import QuoteItem, StoreApiError, StoreClient
from norivy_kiosk.store_api.auth import TokenManager


def _client(password: str = mock_store.PASSWORD) -> StoreClient:
    http = httpx.AsyncClient(
        base_url="http://tienda.test",
        transport=httpx.ASGITransport(app=mock_store.create_mock_app()),
    )
    tokens = TokenManager(http, user=mock_store.USER, password=password, skew_s=0.0)
    return StoreClient(http, tokens)


async def test_busqueda_por_modelo_y_ano_filtra_por_aplicabilidad() -> None:
    client = _client()

    found = await client.search_products(text="pastillas", model="CB 190R", year=2020)
    assert [p.id for p in found] == ["r1"]

    none = await client.search_products(text="pastillas", model="CB 190R", year=2010)
    assert none == []
    await client.aclose()


async def test_busqueda_por_tipo_incluye_bicicletas() -> None:
    client = _client()
    bikes = await client.search_products(kind="bicicleta")
    assert [p.name for p in bikes] == ["Bicicleta Montañera GW Rin 29"]
    await client.aclose()


async def test_aplicabilidad_devuelve_las_motos() -> None:
    client = _client()
    fits = await client.get_applicability("r3")
    assert (fits[0].brand, fits[0].model, fits[0].year_from) == (
        "Yamaha",
        "FZ 2.0",
        2017,
    )
    await client.aclose()


async def test_cotizacion_end_to_end_con_pdf_descargable() -> None:
    client = _client()

    quote = await client.create_quote([QuoteItem("r1", 2), QuoteItem("r2", 1)])

    assert quote.total == pytest.approx(18.5 * 2 + 6.9)
    pdf = await client._http.get(quote.pdf_url)  # el celular entra sin token
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    await client.aclose()


async def test_credenciales_malas_dan_error_apto_para_decir_en_voz_alta() -> None:
    client = _client(password="mala")
    with pytest.raises(StoreApiError, match="conectarme"):
        await client.search_products(text="x")
    await client.aclose()


async def test_token_vencido_se_renueva_solo() -> None:
    client = _client()
    await client.search_products(text="filtro")
    client._tokens._expires_at = 0.0  # el token del cliente venció localmente
    # (el servidor sigue aceptándolo; con refresh se renueva y la llamada sigue OK)
    assert [p.id for p in await client.search_products(text="filtro")] == ["r2"]
    await client.aclose()
