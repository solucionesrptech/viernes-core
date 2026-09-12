"""Recepción temporal de audio desde nodos de habitación + STT V0."""

from __future__ import annotations

import logging
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import UploadFile

from rooms import get_room_base_url
from intents import process_voice_command
from stt import (
    STTError,
    STTInvalidAudioError,
    STTTranscriptionError,
    STTUnavailableError,
    transcribe_audio,
)

logger = logging.getLogger("viernes.audio")

# Temporal de proceso; no es almacenamiento permanente.
AUDIO_TMP_DIR = Path(tempfile.gettempdir()) / "viernes-room-audio"
_SAFE_SUFFIX = re.compile(r"^\.[a-zA-Z0-9]{1,8}$")
MAX_AUDIO_BYTES = 8 * 1024 * 1024  # 8 MiB V0


def ensure_audio_tmp_dir() -> Path:
    AUDIO_TMP_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_TMP_DIR


def _safe_suffix(filename: str | None) -> str:
    if not filename:
        return ".m4a"
    suffix = Path(filename).suffix.lower()
    if suffix and _SAFE_SUFFIX.match(suffix):
        return suffix
    return ".m4a"


async def save_room_audio(room: str, upload: UploadFile) -> dict:
    """Valida room, guarda temp, transcribe y devuelve texto + metadata."""
    room_key = room.strip().casefold()
    try:
        get_room_base_url(room_key)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    ensure_audio_tmp_dir()
    suffix = _safe_suffix(upload.filename)
    stored_name = f"{room_key}_{uuid.uuid4().hex}{suffix}"
    dest = AUDIO_TMP_DIR / stored_name

    data = await upload.read(MAX_AUDIO_BYTES + 1)
    if len(data) == 0:
        raise ValueError("Archivo de audio vacío.")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Archivo de audio demasiado grande.")

    dest.write_bytes(data)
    nbytes = len(data)
    logger.info(
        "[AUDIO] saved room=%s filename=%s bytes=%s path=%s",
        room_key,
        stored_name,
        nbytes,
        dest,
    )

    payload: dict = {
        "success": True,
        "room": room_key,
        "bytes": nbytes,
        "filename": stored_name,
        "text": None,
    }

    logger.info("[STT] room=%s transcribing ...", room_key)
    try:
        text = transcribe_audio(dest)
    except STTUnavailableError as exc:
        logger.error("[STT] unavailable room=%s err=%s", room_key, exc)
        payload["success"] = False
        payload["error"] = "stt_unavailable"
        payload["message"] = str(exc)
        return payload
    except STTInvalidAudioError as exc:
        logger.error("[STT] invalid audio room=%s err=%s", room_key, exc)
        payload["success"] = False
        payload["error"] = "invalid_audio"
        payload["message"] = str(exc)
        return payload
    except STTTranscriptionError as exc:
        logger.error("[STT] transcription failed room=%s err=%s", room_key, exc)
        payload["success"] = False
        payload["error"] = "stt_failed"
        payload["message"] = str(exc)
        return payload
    except STTError as exc:
        logger.error("[STT] error room=%s err=%s", room_key, exc)
        payload["success"] = False
        payload["error"] = "stt_error"
        payload["message"] = str(exc)
        return payload

    payload["text"] = text
    payload["intent"] = None
    payload["reply"] = None
    payload["message"] = "ok"

    try:
        voiced = process_voice_command(room_key, text)
        payload["intent"] = voiced.get("intent")
        payload["reply"] = voiced.get("reply")
        payload["target_room"] = voiced.get("target_room")
        payload["query"] = voiced.get("query")
    except Exception as exc:  # noqa: BLE001 — no tumbar el endpoint de audio
        logger.error("[VOICE] process failed room=%s err=%s", room_key, exc)
        payload["message"] = "transcribed; intent processing failed"

    return payload
