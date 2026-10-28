# norivy-kiosk — Asistente de voz para pantalla pública de tienda (Electron + gpt-realtime-2.1 + API de tienda)

> Inherits: ../CLAUDE.md. Fork de `norivy-stage-v2` (duplicado con conciencia, sin imports cruzados).

## Qué es

Pantalla inteligente de una tienda de motos y bicicletas. El cliente toca la pantalla (`start`),
habla con Norivy y ve resultados en pantalla. Motor ÚNICO = OpenAI Realtime speech-to-speech.
Sin pipeline Whisper/VAD-STT-LLM-TTS.

## Decisiones

- **Dos procesos**: backend Python (FastAPI + sounddevice) y Electron (renderer), por WS local
  `ws://127.0.0.1:8773/ws/kiosk` (8771 = stage-v2, 8772 = desktop). Solo `/healthz` por HTTP.
- **El modelo NUNCA sabe precios/stock/compatibilidad**: todo sale de las tools
  (`tools.py`: `buscar_productos`, `ver_aplicabilidad`, `agregar_a_cotizacion`,
  `quitar_de_cotizacion`, `generar_cotizacion`). El prompt (`persona.py`) es la primera barrera del
  topic-lock; la segunda es que solo hay tools de catálogo.
- **El carrito solo acepta ids que el modelo vio en una búsqueda** (`ToolRunner._known`): evita ids/precios alucinados.
- **Credenciales de la tienda solo en el backend** (`store_api/`): login del usuario de servicio,
  accessToken en memoria, renovación con refresh/login, 401 → reintento único. Las rutas y nombres de
  campo de la API viven SOLO en `store_api/auth.py` y `store_api/client.py` (contrato SUPUESTO,
  ver abajo): al llegar la documentación real se ajusta ahí y en `mock_store.py`.
- **La URL de la cotización la abre el celular del cliente SIN token** → la API debe devolver un enlace
  público o firmado y de vida corta. El renderer solo acepta URLs http(s) (`safeHttpUrl`).
- **Privacidad entre clientes**: `end_session` (botón «Terminar» o inactividad 60 s; 120 s con QR en
  pantalla) mutea el mic, vacía el carrito y **reinicia el WS de OpenAI** (`client.restart()`) para
  borrar la conversación. No se piden datos personales; solo nombre opcional para la cotización.
- **Mic muteado hasta el toque**: `begin_session` abre el mic y dispara el saludo (el toque desbloquea
  el audio del navegador). Sin tráfico de audio con la pantalla en atracción.
- **Half-duplex por defecto** (parlantes abiertos): ver `_should_gate_mic` y `HALF_DUPLEX_TAIL_S`.
- **El renderer pinta solo con `textContent`** (los datos vienen de la API): nunca `innerHTML` con datos
  de la tienda (el QR es SVG generado por `uqr`, no HTML de la API).
- Lógica pura de la pantalla en `avatar/lib/kioskView.js` (testeada con Vitest).

## Protocolo WS

- Server→client: `hello{character_name, store_name, model, voice, mic_muted, realtime_active}` ·
  `status{state}` (incluye `working` mientras corre una tool) · `speech_started` · `audio{pcm16}` ·
  `transcript{delta}` · `speech_done` · `user_speech_started` · `user_transcript{delta}` ·
  `user_transcript_done{transcript}` · `mic_state{muted}` · `error{message}` ·
  **`product_results{items}`** · **`applicability{product_id, product_name, items}`** ·
  **`quote_cart{items,total}`** · **`quote_ready{quote_id,url,total,currency}`** · **`session_reset`**.
- Client→server: `start` (toque en pantalla) · `reset` (Terminar / inactividad).
- Audio: base64 PCM16 mono 24 kHz en ambos sentidos.

## Contrato SUPUESTO de la API de la tienda (pendiente de confirmar con la documentación real)

- `POST /auth/login {email,password}` → `{accessToken, refreshToken?, expiresIn?}` · `POST /auth/refresh {refreshToken}`.
- `GET /products?q&type&brand&model&year&limit` → `{items:[{id,name,sku,brand,category,type,price,currency,stock,imageUrl}]}`.
- `GET /products/{id}/applicability` → `{items:[{brand,model,yearFrom,yearTo,notes}]}`.
- `POST /quotes {items:[{productId,quantity}], customerName?}` → `{id,total,currency,pdfUrl}`.
- `store_api` acepta camelCase y snake_case y respuestas `[...]` o `{items|data|results:[...]}`.

## Pendiente / sin hacer

- Contrato real de la API (arriba). Logo/branding de la tienda (hoy solo el nombre en la barra).
- Interacción táctil sobre las tarjetas (hoy todo se dirige por voz; solo «Terminar»).
- E2E de Electron (`avatar/e2e/kiosk.e2e.js`) escrito pero NO ejecutado (sin binario de Electron donde se desarrolló).
- Calibración de VAD/ruido con el micrófono y los parlantes reales de la tienda.
- Detección de presencia (cámara) para saludar sin tocar: no incluida.
