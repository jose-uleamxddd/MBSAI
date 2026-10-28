"""Entrypoint — ``python -m norivy_kiosk``.

Comentario en español: arranca uvicorn con la app FastAPI en el host/puerto de
config. Electron spawnea esto como ``python -m norivy_kiosk`` con PYTHONPATH=src.
"""

from __future__ import annotations

import uvicorn

from . import config
from .app import create_app

app = create_app()


def main() -> None:
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
