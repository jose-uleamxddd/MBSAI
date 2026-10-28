"""ToolRunner: validación, carrito por sesión y frames hacia la pantalla."""

from __future__ import annotations

import json
from typing import Any

import pytest

from norivy_kiosk.store_api import Fitment, Product, Quote, QuoteItem, StoreApiError
from norivy_kiosk.tools import TOOLS, ToolRunner


def _product(pid: str, price: float = 10.0) -> Product:
    return Product(
        pid, f"Prod {pid}", "SKU", "Marca", "Cat", "repuesto", price, "USD", 5, ""
    )


class _FakeStore:
    def __init__(self) -> None:
        self.products = [_product("r1", 18.5), _product("r2", 6.9)]
        self.fail = False
        self.search_args: dict[str, Any] = {}
        self.quote_items: list[QuoteItem] = []
        self.closed = False

    async def search_products(self, **kwargs: Any) -> list[Product]:
        if self.fail:
            raise StoreApiError("El sistema de la tienda no responde")
        self.search_args = kwargs
        return self.products

    async def get_applicability(self, product_id: str) -> list[Fitment]:
        return [Fitment("Honda", "CB 190R", 2016, 2024, "")]

    async def create_quote(
        self, items: list[QuoteItem], customer_name: str = ""
    ) -> Quote:
        self.quote_items = items
        return Quote("C1", 43.9, "USD", "http://tienda.test/q.pdf")

    async def aclose(self) -> None:
        self.closed = True


def _runner() -> tuple[ToolRunner, _FakeStore, list[dict[str, Any]]]:
    frames: list[dict[str, Any]] = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    store = _FakeStore()
    return ToolRunner(store, emit), store, frames  # type: ignore[arg-type]


async def _run(runner: ToolRunner, name: str, **args: Any) -> dict[str, Any]:
    return json.loads(await runner.run(name, json.dumps(args)))


def test_todas_las_tools_tienen_schema_function_valido() -> None:
    names = [t["name"] for t in TOOLS]
    assert names == [
        "buscar_productos",
        "ver_aplicabilidad",
        "agregar_a_cotizacion",
        "quitar_de_cotizacion",
        "generar_cotizacion",
    ]
    assert all(
        t["type"] == "function" and t["parameters"]["type"] == "object" for t in TOOLS
    )


async def test_buscar_emite_tarjetas_y_pasa_los_filtros() -> None:
    runner, store, frames = _runner()

    out = await _run(
        runner, "buscar_productos", texto="pastillas", modelo="CB 190R", anio=2020
    )

    assert out["ok"] is True and out["cantidad"] == 2
    assert store.search_args["model"] == "CB 190R" and store.search_args["year"] == 2020
    assert frames[0]["type"] == "product_results"
    assert [i["id"] for i in frames[0]["items"]] == ["r1", "r2"]


async def test_buscar_rechaza_tipo_invalido() -> None:
    runner, _, _ = _runner()
    out = await _run(runner, "buscar_productos", tipo="auto")
    assert out["ok"] is False


async def test_agregar_exige_haber_buscado_antes() -> None:
    runner, _, frames = _runner()
    out = await _run(
        runner, "agregar_a_cotizacion", producto_id="inventado", cantidad=1
    )
    assert out["ok"] is False
    assert frames == []


@pytest.mark.parametrize("qty", [0, 100])
async def test_agregar_valida_el_rango_de_cantidad(qty: int) -> None:
    runner, _, _ = _runner()
    await _run(runner, "buscar_productos")
    out = await _run(runner, "agregar_a_cotizacion", producto_id="r1", cantidad=qty)
    assert out["ok"] is False


async def test_carrito_suma_cantidades_y_emite_total() -> None:
    runner, _, frames = _runner()
    await _run(runner, "buscar_productos")
    await _run(runner, "agregar_a_cotizacion", producto_id="r1", cantidad=2)
    await _run(runner, "agregar_a_cotizacion", producto_id="r1", cantidad=1)
    await _run(runner, "agregar_a_cotizacion", producto_id="r2", cantidad=1)

    cart = frames[-1]
    assert cart["type"] == "quote_cart"
    assert {i["product_id"]: i["quantity"] for i in cart["items"]} == {"r1": 3, "r2": 1}
    assert cart["total"] == pytest.approx(18.5 * 3 + 6.9)


async def test_quitar_producto_ausente_falla() -> None:
    runner, _, _ = _runner()
    assert (await _run(runner, "quitar_de_cotizacion", producto_id="r1"))["ok"] is False


async def test_generar_con_carrito_vacio_falla() -> None:
    runner, _, _ = _runner()
    assert (await _run(runner, "generar_cotizacion"))["ok"] is False


async def test_generar_emite_quote_ready_y_vacia_el_carrito() -> None:
    runner, store, frames = _runner()
    await _run(runner, "buscar_productos")
    await _run(runner, "agregar_a_cotizacion", producto_id="r1", cantidad=2)

    out = await _run(runner, "generar_cotizacion", nombre_cliente="Ana")

    assert out["ok"] is True and out["total"] == pytest.approx(43.9)
    assert store.quote_items == [QuoteItem("r1", 2)]
    assert frames[-1] == {
        "type": "quote_ready",
        "quote_id": "C1",
        "url": "http://tienda.test/q.pdf",
        "total": 43.9,
        "currency": "USD",
    }
    assert (await _run(runner, "generar_cotizacion"))["ok"] is False  # carrito vacío


async def test_aplicabilidad_emite_frame_y_responde() -> None:
    runner, _, frames = _runner()
    await _run(runner, "buscar_productos")
    out = await _run(runner, "ver_aplicabilidad", producto_id="r1")
    assert out["aplica_a"][0]["modelo"] == "CB 190R"
    assert frames[-1]["type"] == "applicability"


async def test_error_de_la_tienda_llega_al_modelo_sin_propagar() -> None:
    runner, store, _ = _runner()
    store.fail = True
    out = await _run(runner, "buscar_productos")
    assert out == {"ok": False, "error": "El sistema de la tienda no responde"}


async def test_tool_desconocida_y_argumentos_invalidos() -> None:
    runner, _, _ = _runner()
    assert json.loads(await runner.run("borrar_todo", "{}"))["ok"] is False
    assert json.loads(await runner.run("buscar_productos", "[1]"))["ok"] is False
    assert json.loads(await runner.run("buscar_productos", "{no json"))["ok"] is False


async def test_reset_vacia_carrito_y_productos_vistos() -> None:
    runner, store, _ = _runner()
    await _run(runner, "buscar_productos")
    await _run(runner, "agregar_a_cotizacion", producto_id="r1", cantidad=1)

    runner.reset()

    out = await _run(runner, "agregar_a_cotizacion", producto_id="r1", cantidad=1)
    assert out["ok"] is False  # ya no «conoce» el producto


async def test_aclose_cierra_la_tienda() -> None:
    runner, store, _ = _runner()
    await runner.aclose()
    assert store.closed is True
