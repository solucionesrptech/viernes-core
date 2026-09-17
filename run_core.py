"""Arranque del Core como tarea de Windows: sin consola y con log rotado."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = "8000"
_LOG_DIR = Path(__file__).with_name("logs")
_LOG_FILE = _LOG_DIR / "core.log"
_LOG_MAX_BYTES = 2_000_000
_LOG_BACKUPS = 3


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _setup_logging() -> None:
    """Deja el log en archivo antes de importar la app."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUPS,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # pythonw deja stdout/stderr en None: cualquier escritura directa rompería.
    devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = devnull
    sys.stderr = devnull


def main() -> None:
    # El handler debe existir antes de importar app.py, cuyo basicConfig
    # escribiría a un stderr que aquí no existe.
    _setup_logging()

    import uvicorn

    host = _env("VIERNES_CORE_HOST", _DEFAULT_HOST)
    port = int(_env("VIERNES_CORE_PORT", _DEFAULT_PORT))
    logging.getLogger("viernes.boot").info("[BOOT] host=%s port=%d", host, port)

    uvicorn.run("app:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
