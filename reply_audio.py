"""WAV temporales de reply TTS (solo generados por Viernes)."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger("viernes.reply_audio")

REPLY_WAV_DIR = Path(tempfile.gettempdir()) / "viernes-reply-tts"
_SAFE_NAME = re.compile(r"^[a-f0-9]{32}\.wav$")


def get_core_public_base() -> str:
    """Base LAN del Core. Override: VIERNES_CORE_PUBLIC_URL."""
    return (
        os.environ.get("VIERNES_CORE_PUBLIC_URL", "http://192.168.1.104:8000")
        .strip()
        .rstrip("/")
    )


def ensure_reply_wav_dir() -> Path:
    REPLY_WAV_DIR.mkdir(parents=True, exist_ok=True)
    return REPLY_WAV_DIR


def store_reply_wav(wav_bytes: bytes) -> tuple[str, str]:
    """Guarda WAV único. Devuelve (filename, url pública LAN)."""
    if not wav_bytes:
        raise ValueError("WAV vacío.")
    ensure_reply_wav_dir()
    filename = f"{uuid.uuid4().hex}.wav"
    dest = REPLY_WAV_DIR / filename
    dest.write_bytes(wav_bytes)
    url = f"{get_core_public_base()}/tts/tmp/{filename}"
    logger.info("[TTS] stored filename=%s bytes=%s url=%s", filename, len(wav_bytes), url)
    return filename, url


def resolve_reply_wav(filename: str) -> Path | None:
    """Resuelve un WAV solo si el nombre es el allowlist de Viernes y existe."""
    name = (filename or "").strip()
    if not _SAFE_NAME.match(name):
        return None
    path = (REPLY_WAV_DIR / name).resolve()
    try:
        path.relative_to(REPLY_WAV_DIR.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path
