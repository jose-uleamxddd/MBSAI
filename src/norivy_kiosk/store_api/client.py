"""Cliente HTTP de la tienda: catálogo, aplicabilidad y cotizaciones.

Comentario en español: todas las llamadas llevan el Bearer del ``TokenManager``;
ante un 401 se invalida el token y se reintenta UNA vez. Los errores se traducen a
``StoreApiError`` con un mensaje que Norivy puede decir en voz alta (sin detalles
internos). Los formatos de respuesta se normalizan a dataclasses: si la API real
difiere, solo se ajustan los ``_parse_*`` de este archivo.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from .. import config
from .auth import AuthError, TokenManager, pick

logger = logging.getLogger(__name__)

PRODUCTS_PATH = "/products"
QUOTES_PATH = "/quotes"


class StoreApiError(Exception):
    """Fallo al hablar con la tienda; ``str(exc)`` es apto para decir al cliente."""


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    sku: str
    brand: str
    category: str
    kind: str  # moto | bicicleta | repuesto | accesorio
    price: float
    currency: str
    stock: int | None
    image_url: str


@dataclass(frozen=True)
class Fitment:
    brand: str
    model: str
    year_from: int | None
    year_to: int | None
    notes: str


@dataclass(frozen=True)
class QuoteItem:
    product_id: str
    quantity: int


@dataclass(frozen=True)
class Quote:
    id: str
    total: float
    currency: str
    pdf_url: str


def _items(data: Any) -> list[dict[str, Any]]:
    """Lista de objetos de una respuesta (`[...]` o `{items|data|results: [...]}`)."""
    if isinstance(data, dict):
        data = pick(data, "items", "data", "results", default=[])
    return (
        [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_product(row: dict[str, Any]) -> Product:
    return Product(
        id=str(pick(row, "id", "productId", default="")),
        name=str(pick(row, "name", "title", default="")),
        sku=str(pick(row, "sku", "code", default="")),
        brand=str(pick(row, "brand", default="")),
        category=str(pick(row, "category", default="")),
        kind=str(pick(row, "type", "kind", default="")),
        price=float(pick(row, "price", default=0) or 0),
        currency=str(pick(row, "currency", default="USD")),
        stock=_int_or_none(pick(row, "stock", "quantity")),
        image_url=str(pick(row, "imageUrl", "image_url", "image", default="")),
    )


def _parse_fitment(row: dict[str, Any]) -> Fitment:
    return Fitment(
        brand=str(pick(row, "brand", default="")),
        model=str(pick(row, "model", default="")),
        year_from=_int_or_none(pick(row, "yearFrom", "year_from")),
        year_to=_int_or_none(pick(row, "yearTo", "year_to")),
        notes=str(pick(row, "notes", default="")),
    )


class StoreClient:
    """Fachada async sobre la API de la tienda (una instancia por backend)."""

    def __init__(self, http: httpx.AsyncClient, tokens: TokenManager) -> None:
        self._http = http
        self._tokens = tokens

    @classmethod
    def from_config(cls) -> StoreClient:
        """Arma el cliente con las credenciales del `.env` (falla si faltan)."""
        url, user, password = config.require_store_credentials()
        http = httpx.AsyncClient(base_url=url, timeout=config.STORE_API_TIMEOUT_S)
        tokens = TokenManager(
            http, user=user, password=password, skew_s=config.STORE_TOKEN_SKEW_S
        )
        return cls(http, tokens)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Request autenticada; 401 → renueva el token y reintenta una sola vez."""
        for attempt in (1, 2):
            try:
                token = await self._tokens.get()
                resp = await self._http.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except AuthError as exc:
                logger.warning("[store] auth falló: %s", exc)
                raise StoreApiError(
                    "No pude conectarme al sistema de la tienda"
                ) from exc
            except httpx.HTTPError as exc:
                logger.warning("[store] %s %s falló: %s", method, path, exc)
                raise StoreApiError("El sistema de la tienda no responde") from exc
            if resp.status_code == 401 and attempt == 1:
                self._tokens.invalidate()
                continue
            if resp.status_code >= 400:
                logger.warning(
                    "[store] %s %s → HTTP %s", method, path, resp.status_code
                )
                raise StoreApiError(
                    f"La tienda respondió con error ({resp.status_code})"
                )
            return resp.json()
        raise StoreApiError("No tengo autorización en el sistema de la tienda")

    async def search_products(
        self,
        *,
        text: str = "",
        kind: str = "",
        brand: str = "",
        model: str = "",
        year: int | None = None,
        limit: int = config.SEARCH_MAX_RESULTS,
    ) -> list[Product]:
        """Busca productos; los filtros vacíos no se envían."""
        params: dict[str, Any] = {
            "q": text,
            "type": kind,
            "brand": brand,
            "model": model,
            "year": year,
            "limit": limit,
        }
        data = await self._request(
            "GET", PRODUCTS_PATH, params={k: v for k, v in params.items() if v}
        )
        return [_parse_product(row) for row in _items(data)][:limit]

    async def get_applicability(self, product_id: str) -> list[Fitment]:
        """Motos/bicicletas a las que se aplica un repuesto."""
        data = await self._request("GET", f"{PRODUCTS_PATH}/{product_id}/applicability")
        return [_parse_fitment(row) for row in _items(data)]

    async def create_quote(
        self, items: list[QuoteItem], customer_name: str = ""
    ) -> Quote:
        """Crea la cotización en la tienda y devuelve el enlace de descarga."""
        body: dict[str, Any] = {
            "items": [
                {"productId": it.product_id, "quantity": it.quantity} for it in items
            ]
        }
        if customer_name:
            body["customerName"] = customer_name
        data = await self._request("POST", QUOTES_PATH, json=body)
        if not isinstance(data, dict):
            raise StoreApiError("La tienda devolvió una cotización inválida")
        pdf_url = str(pick(data, "pdfUrl", "pdf_url", "downloadUrl", default=""))
        if not pdf_url:
            raise StoreApiError("La cotización no trae enlace de descarga")
        # Enlace relativo → absoluto (el celular del cliente lo abre desde el QR).
        pdf_url = str(self._http.base_url.join(pdf_url))
        return Quote(
            id=str(pick(data, "id", "quoteId", default="")),
            total=float(pick(data, "total", default=0) or 0),
            currency=str(pick(data, "currency", default="USD")),
            pdf_url=pdf_url,
        )


def product_dict(product: Product) -> dict[str, Any]:
    """Producto como dict JSON (para el modelo y para las tarjetas de la UI)."""
    return asdict(product)
