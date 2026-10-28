"""Subpaquete realtime — captura de mic + cliente OpenAI Realtime + sesión.

Comentario en español: motor speech-to-speech de Norivy. `audio` captura el mic
con sounddevice, `client` habla el WebSocket GA de OpenAI y `session` puentea
ambos con el Hub local.
"""

from __future__ import annotations
