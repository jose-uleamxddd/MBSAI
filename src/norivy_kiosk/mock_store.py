"""API de tienda de MENTIRA para desarrollar y probar el kiosco sin el sistema real.

Comentario en español: imita el contrato que asume ``store_api`` (login con
accessToken que vence, catálogo, aplicabilidad y cotizaciones con PDF público).
Arranque: ``python -m norivy_kiosk.mock_store`` (puerto 8780) y apunta
``NORIVY_KIOSK_STORE_URL=http://127.0.0.1:8780`` con usuario ``norivy@tienda.local``
y contraseña ``norivy123``. Al conectar la API real, este módulo no se usa.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Response

USER = "norivy@tienda.local"
PASSWORD = "norivy123"
TOKEN_TTL_S = 120

CATALOG: list[dict[str, Any]] = [
    {
        "id": "m1",
        "name": "Honda CB 190R",
        "sku": "MOT-CB190R",
        "brand": "Honda",
        "category": "Motos",
        "type": "moto",
        "price": 3290,
        "stock": 4,
    },
    {
        "id": "m2",
        "name": "Yamaha FZ 2.0",
        "sku": "MOT-FZ20",
        "brand": "Yamaha",
        "category": "Motos",
        "type": "moto",
        "price": 3490,
        "stock": 2,
    },
    {
        "id": "b1",
        "name": "Bicicleta Montañera GW Rin 29",
        "sku": "BIC-GW29",
        "brand": "GW",
        "category": "Bicicletas",
        "type": "bicicleta",
        "price": 520,
        "stock": 7,
    },
    {
        "id": "r1",
        "name": "Pastillas de freno delanteras",
        "sku": "REP-PF-001",
        "brand": "Brembo",
        "category": "Frenos",
        "type": "repuesto",
        "price": 18.5,
        "stock": 25,
    },
    {
        "id": "r2",
        "name": "Filtro de aceite",
        "sku": "REP-FA-014",
        "brand": "HiFlo",
        "category": "Motor",
        "type": "repuesto",
        "price": 6.9,
        "stock": 60,
    },
    {
        "id": "r3",
        "name": "Kit de arrastre (cadena y piñones)",
        "sku": "REP-KA-220",
        "brand": "DID",
        "category": "Transmisión",
        "type": "repuesto",
        "price": 74,
        "stock": 9,
    },
    {
        "id": "r4",
        "name": "Batería 12V 6Ah",
        "sku": "REP-BT-612",
        "brand": "Yuasa",
        "category": "Eléctrico",
        "type": "repuesto",
        "price": 39,
        "stock": 0,
    },
    {
        "id": "a1",
        "name": "Casco integral certificado",
        "sku": "ACC-CI-050",
        "brand": "LS2",
        "category": "Protección",
        "type": "accesorio",
        "price": 85,
        "stock": 12,
    },
]

FITMENTS: dict[str, list[dict[str, Any]]] = {
    "r1": [
        {
            "brand": "Honda",
            "model": "CB 190R",
            "yearFrom": 2016,
            "yearTo": 2024,
            "notes": "Eje delantero",
        },
        {
            "brand": "Yamaha",
            "model": "FZ 2.0",
            "yearFrom": 2017,
            "yearTo": 2023,
            "notes": "",
        },
    ],
    "r2": [
        {
            "brand": "Honda",
            "model": "CB 190R",
            "yearFrom": 2016,
            "yearTo": 2024,
            "notes": "",
        },
        {
            "brand": "Honda",
            "model": "XR 150L",
            "yearFrom": 2014,
            "yearTo": 2024,
            "notes": "",
        },
    ],
    "r3": [
        {
            "brand": "Yamaha",
            "model": "FZ 2.0",
            "yearFrom": 2017,
            "yearTo": 2023,
            "notes": "Paso 520, 42 dientes",
        }
    ],
    "r4": [
        {
            "brand": "Honda",
            "model": "CB 190R",
            "yearFrom": 2016,
            "yearTo": 2024,
            "notes": "",
        }
    ],
}

_PDF = (
    b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _fits(product_id: str, model: str, year: int | None) -> bool:
    rows = FITMENTS.get(product_id, [])
    return any(
        model.lower() in f"{r['brand']} {r['model']}".lower()
        and (year is None or r["yearFrom"] <= year <= r["yearTo"])
        for r in rows
    )


def create_mock_app() -> FastAPI:  # noqa: C901 — mock: una ruta por endpoint
    app = FastAPI(title="mock-store")
    tokens: dict[str, float] = {}  # accessToken → vencimiento (epoch)
    refresh_tokens: set[str] = set()
    quotes: dict[str, dict[str, Any]] = {}

    def issue() -> dict[str, Any]:
        access, refresh = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        tokens[access] = time.time() + TOKEN_TTL_S
        refresh_tokens.add(refresh)
        return {
            "accessToken": access,
            "refreshToken": refresh,
            "expiresIn": TOKEN_TTL_S,
        }

    def auth(authorization: str = Header(default="")) -> None:
        token = authorization.removeprefix("Bearer ").strip()
        if tokens.get(token, 0) < time.time():
            raise HTTPException(status_code=401, detail="token inválido o vencido")

    @app.post("/auth/login")
    async def login(body: dict[str, str]) -> dict[str, Any]:
        if body.get("email") != USER or body.get("password") != PASSWORD:
            raise HTTPException(status_code=401, detail="credenciales inválidas")
        return issue()

    @app.post("/auth/refresh")
    async def refresh(body: dict[str, str]) -> dict[str, Any]:
        if body.get("refreshToken") not in refresh_tokens:
            raise HTTPException(status_code=401, detail="refresh inválido")
        refresh_tokens.discard(body["refreshToken"])
        return issue()

    @app.get("/products", dependencies=[Depends(auth)])
    async def products(
        q: str = "",
        type: str = "",
        brand: str = "",
        model: str = "",
        year: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        rows = [
            p
            for p in CATALOG
            if (
                not q
                or q.lower() in f"{p['name']} {p['category']} {p['brand']}".lower()
            )
            and (not type or p["type"] == type)
            and (not brand or brand.lower() in p["brand"].lower())
            and (not (model or year) or _fits(p["id"], model, year))
        ]
        return {"items": rows[:limit]}

    @app.get("/products/{product_id}/applicability", dependencies=[Depends(auth)])
    async def applicability(product_id: str) -> dict[str, Any]:
        return {"items": FITMENTS.get(product_id, [])}

    @app.post("/quotes", dependencies=[Depends(auth)])
    async def create_quote(body: dict[str, Any]) -> dict[str, Any]:
        by_id = {p["id"]: p for p in CATALOG}
        total = sum(
            by_id[i["productId"]]["price"] * i["quantity"] for i in body["items"]
        )
        quote_id = f"COT-{len(quotes) + 1:04d}"
        quotes[quote_id] = body
        # PDF PÚBLICO (el celular no tiene token): en producción, enlace firmado.
        return {
            "id": quote_id,
            "total": total,
            "currency": "USD",
            "pdfUrl": f"/quotes/{quote_id}.pdf",
        }

    @app.get("/quotes/{quote_id}.pdf")
    async def quote_pdf(quote_id: str) -> Response:
        if quote_id not in quotes:
            raise HTTPException(status_code=404)
        return Response(_PDF, media_type="application/pdf")

    return app


if __name__ == "__main__":
    uvicorn.run(create_mock_app(), host="127.0.0.1", port=8780)
