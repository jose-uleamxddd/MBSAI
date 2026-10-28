"""Tools (function calling) de Norivy: catálogo, aplicabilidad y cotización.

Comentario en español: ``TOOLS`` es el catálogo que se manda a OpenAI en el
``session.update``; ``ToolRunner.run`` ejecuta una llamada contra la API de la
tienda y devuelve el JSON que el modelo usará para responder. Además de la
respuesta al modelo, cada tool EMITE un frame al renderer (tarjetas de productos,
aplicabilidad, carrito, cotización con QR) para que el cliente lo vea en pantalla.
El carrito es por sesión: ``reset()`` lo vacía al terminar con un cliente.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .store_api import Product, QuoteItem, StoreApiError, StoreClient
from .store_api.client import product_dict

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]

KINDS = ["moto", "bicicleta", "repuesto", "accesorio"]
MAX_QUANTITY = 99

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "buscar_productos",
        "description": (
            "Busca en el catálogo de la tienda (motos, bicicletas, repuestos y "
            "accesorios). Usa los filtros que el cliente haya dado. Devuelve "
            "productos con id, precio y stock; el cliente los ve en pantalla."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Palabras clave libres."},
                "tipo": {"type": "string", "enum": KINDS},
                "marca": {"type": "string", "description": "Marca del producto."},
                "modelo": {
                    "type": "string",
                    "description": "Modelo de la moto/bicicleta a la que debe aplicar.",
                },
                "anio": {"type": "integer", "description": "Año de la moto."},
            },
        },
    },
    {
        "type": "function",
        "name": "ver_aplicabilidad",
        "description": (
            "Lista las motos o bicicletas a las que se aplica un repuesto. "
            "Úsala siempre antes de afirmar que algo es compatible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "producto_id": {
                    "type": "string",
                    "description": "Id devuelto por buscar_productos.",
                }
            },
            "required": ["producto_id"],
        },
    },
    {
        "type": "function",
        "name": "agregar_a_cotizacion",
        "description": (
            "Agrega un producto (ya encontrado con buscar_productos) a la "
            "cotización del cliente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "producto_id": {"type": "string"},
                "cantidad": {"type": "integer", "minimum": 1, "maximum": MAX_QUANTITY},
            },
            "required": ["producto_id", "cantidad"],
        },
    },
    {
        "type": "function",
        "name": "quitar_de_cotizacion",
        "description": "Quita un producto de la cotización en curso.",
        "parameters": {
            "type": "object",
            "properties": {"producto_id": {"type": "string"}},
            "required": ["producto_id"],
        },
    },
    {
        "type": "function",
        "name": "generar_cotizacion",
        "description": (
            "Genera la cotización final con los productos agregados. La pantalla "
            "muestra un código QR para que el cliente la descargue en su celular."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nombre_cliente": {
                    "type": "string",
                    "description": "Opcional: solo si el cliente lo dijo.",
                }
            },
        },
    },
]


def _ok(**data: Any) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False)


def _fail(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


class ToolRunner:
    """Ejecuta las tools contra la tienda y mantiene el carrito de la sesión."""

    def __init__(self, store: StoreClient, emit: Emit) -> None:
        self._store = store
        self._emit = emit
        # Productos vistos en búsquedas: el carrito solo acepta ids que el modelo
        # realmente encontró (evita que alucine ids o precios).
        self._known: dict[str, Product] = {}
        self._cart: dict[str, int] = {}

    def reset(self) -> None:
        """Vacía el carrito y lo visto (nuevo cliente)."""
        self._known.clear()
        self._cart.clear()

    async def aclose(self) -> None:
        await self._store.aclose()

    async def run(self, name: str, arguments: str) -> str:
        """Ejecuta una tool; SIEMPRE devuelve un JSON (nunca propaga excepciones)."""
        handler = self._handlers().get(name)
        if handler is None:
            return _fail(f"herramienta desconocida: {name}")
        try:
            args = json.loads(arguments) if arguments else {}
            if not isinstance(args, dict):
                return _fail("argumentos inválidos")
            return await handler(args)
        except StoreApiError as exc:
            return _fail(str(exc))
        except (ValueError, TypeError) as exc:
            return _fail(f"argumentos inválidos: {exc}")
        except Exception:  # noqa: BLE001 — boundary: el modelo debe poder responder
            logger.exception("[tools] %s falló", name)
            return _fail("error interno de la herramienta")

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[str]]]:
        return {
            "buscar_productos": self._buscar,
            "ver_aplicabilidad": self._aplicabilidad,
            "agregar_a_cotizacion": self._agregar,
            "quitar_de_cotizacion": self._quitar,
            "generar_cotizacion": self._generar,
        }

    async def _buscar(self, args: dict[str, Any]) -> str:
        kind = str(args.get("tipo", ""))
        if kind and kind not in KINDS:
            return _fail(f"tipo inválido, usa uno de: {', '.join(KINDS)}")
        year = args.get("anio")
        products = await self._store.search_products(
            text=str(args.get("texto", "")),
            kind=kind,
            brand=str(args.get("marca", "")),
            model=str(args.get("modelo", "")),
            year=int(year) if year else None,
        )
        for product in products:
            self._known[product.id] = product
        await self._emit(
            {"type": "product_results", "items": [product_dict(p) for p in products]}
        )
        return _ok(
            cantidad=len(products),
            productos=[
                {
                    "id": p.id,
                    "nombre": p.name,
                    "marca": p.brand,
                    "tipo": p.kind,
                    "precio": p.price,
                    "moneda": p.currency,
                    "stock": p.stock,
                }
                for p in products
            ],
        )

    async def _aplicabilidad(self, args: dict[str, Any]) -> str:
        product = self._known.get(str(args.get("producto_id", "")))
        if product is None:
            return _fail("producto no encontrado: búscalo primero con buscar_productos")
        fitments = await self._store.get_applicability(product.id)
        await self._emit(
            {
                "type": "applicability",
                "product_id": product.id,
                "product_name": product.name,
                "items": [
                    {
                        "brand": f.brand,
                        "model": f.model,
                        "year_from": f.year_from,
                        "year_to": f.year_to,
                        "notes": f.notes,
                    }
                    for f in fitments
                ],
            }
        )
        return _ok(
            producto=product.name,
            aplica_a=[
                {
                    "marca": f.brand,
                    "modelo": f.model,
                    "desde": f.year_from,
                    "hasta": f.year_to,
                    "notas": f.notes,
                }
                for f in fitments
            ],
        )

    async def _agregar(self, args: dict[str, Any]) -> str:
        product_id = str(args.get("producto_id", ""))
        if product_id not in self._known:
            return _fail("producto no encontrado: búscalo primero con buscar_productos")
        quantity = int(args.get("cantidad", 1))
        if not 1 <= quantity <= MAX_QUANTITY:
            return _fail(f"la cantidad debe estar entre 1 y {MAX_QUANTITY}")
        self._cart[product_id] = self._cart.get(product_id, 0) + quantity
        await self._emit_cart()
        return _ok(en_cotizacion=self._cart_summary())

    async def _quitar(self, args: dict[str, Any]) -> str:
        product_id = str(args.get("producto_id", ""))
        if self._cart.pop(product_id, None) is None:
            return _fail("ese producto no está en la cotización")
        await self._emit_cart()
        return _ok(en_cotizacion=self._cart_summary())

    async def _generar(self, args: dict[str, Any]) -> str:
        if not self._cart:
            return _fail("la cotización está vacía: agrega productos primero")
        quote = await self._store.create_quote(
            [QuoteItem(pid, qty) for pid, qty in self._cart.items()],
            customer_name=str(args.get("nombre_cliente", "")),
        )
        self._cart.clear()
        await self._emit(
            {
                "type": "quote_ready",
                "quote_id": quote.id,
                "url": quote.pdf_url,
                "total": quote.total,
                "currency": quote.currency,
            }
        )
        return _ok(
            total=quote.total,
            moneda=quote.currency,
            instruccion="El cliente debe escanear el código QR de la pantalla.",
        )

    def _cart_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "product_id": pid,
                "name": self._known[pid].name,
                "quantity": qty,
                "price": self._known[pid].price,
                "currency": self._known[pid].currency,
            }
            for pid, qty in self._cart.items()
        ]

    def _cart_summary(self) -> list[dict[str, Any]]:
        return [
            {"id": r["product_id"], "nombre": r["name"], "cantidad": r["quantity"]}
            for r in self._cart_rows()
        ]

    async def _emit_cart(self) -> None:
        rows = self._cart_rows()
        total = sum(r["price"] * r["quantity"] for r in rows)
        await self._emit({"type": "quote_cart", "items": rows, "total": total})


def build_runner(emit: Emit) -> ToolRunner:
    """Runner de producción: cliente de tienda con las credenciales del `.env`."""
    return ToolRunner(StoreClient.from_config(), emit)


__all__ = ["TOOLS", "Emit", "ToolRunner", "build_runner"]
