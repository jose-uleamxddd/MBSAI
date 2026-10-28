# norivy-kiosk — Asistente de voz para la pantalla inteligente de la tienda

Norivy atiende al cliente por **voz + avatar VRM** en una pantalla pública de una tienda de
motos y bicicletas: responde **solo** sobre motos, bicicletas, repuestos y accesorios, filtra
productos, informa **aplicabilidad** (qué repuesto sirve para qué moto) y arma una
**cotización** en la API de la tienda; el cliente la descarga escaneando un **código QR**.

```
Cliente ── voz ──► Electron (avatar, tarjetas, carrito, QR) ◄─ WS local ─► Backend Python
                                                                              │   │
                                                         OpenAI Realtime ◄────┘   └──► API de la tienda
                                                         (gpt-realtime-2.1)           (usuario de servicio)
```

## Arranque rápido (Windows / PowerShell)

```powershell
# 1) Backend
cd norivy-kiosk
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy env.example .env        # completa OPENAI_API_KEY y las credenciales de la tienda

# 2) Frontend (Electron)
cd avatar
pnpm install

# 3) Desarrollo SIN el sistema real: API de tienda de mentira (otra terminal, desde norivy-kiosk)
$env:PYTHONPATH="src"; .venv\Scripts\python -m norivy_kiosk.mock_store

# 4) Kiosco (Electron levanta el backend solo)
cd avatar; pnpm start
```

En el kiosco real salir con **Ctrl+Shift+Q** (solo personal). Tests: `pytest` (backend) y
`pnpm test` en `avatar/` (Vitest).

## Configuración (`.env`)

Ver `env.example`. Obligatorias: `OPENAI_API_KEY`, `NORIVY_KIOSK_STORE_URL`,
`NORIVY_KIOSK_STORE_USER`, `NORIVY_KIOSK_STORE_PASSWORD`. Las credenciales de la tienda viven
**solo en el backend**: nunca llegan a la pantalla ni a OpenAI.

## Calibrar en el local

- `NORIVY_KIOSK_VAD_THRESHOLD` (0–1): sube si responde al ruido de la tienda, baja si no oye al cliente.
- `NORIVY_KIOSK_NOISE_REDUCTION`: `near_field` (cliente pegado al mic) · `far_field` · `off`.
- Con parlantes abiertos el micrófono se silencia mientras Norivy habla (half-duplex); el cliente
  no puede interrumpirla a mitad de frase. Con auriculares/mic direccional se puede reactivar.
- `NORIVY_KIOSK_MIC_DEVICE`: índice o nombre del micrófono si no es el predeterminado.
