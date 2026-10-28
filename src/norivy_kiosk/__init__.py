"""norivy_kiosk — backend Realtime (speech-to-speech) de la anfitriona Norivy.

Comentario en español: paquete del backend Python para norivy-kiosk. Conecta
con OpenAI Realtime GA (gpt-realtime-2), captura el mic con sounddevice y
retransmite audio + transcript al renderer Electron por un WebSocket local.
"""

from __future__ import annotations

__version__ = "2.0.0"
