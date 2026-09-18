"""Recepción temporal de audio desde nodos de habitación + STT V0."""

from __future__ import annotations

import logging
import re
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import UploadFile

from rooms import get_room_base_url
from intents import process_voice_command
from registry import mark_seen, resolve_node_id, resolve_room_for_path
from stt import (
    STTError,
    STTInvalidAudioError,
    STTTranscriptionError,
    STTUnavailableError,
    transcribe_audio,
)
from telemetry import emit, new_session_id

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


def _source_identity(room_key: str) -> tuple[str, str]:
    """path de audio → (nodeId, room lógica). Fallback: path crudo."""
    node_id = resolve_node_id(room_key) or room_key
    room_label = resolve_room_for_path(room_key) or room_key
    return node_id, room_label


async def save_room_audio(room: str, upload: UploadFile) -> dict:
    """Valida room, guarda temp, transcribe y devuelve texto + metadata."""
    room_key = room.strip().casefold()
    try:
        get_room_base_url(room_key)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    source_node, source_room = _source_identity(room_key)
    session_id = new_session_id()
    t0 = time.perf_counter()

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

    # Presencia por tráfico real de audio (no inventa VOICE_DETECTED).
    try:
        mark_seen(source_node)
    except Exception as exc:  # noqa: BLE001
        logger.error("[REGISTRY] mark_seen failed node=%s err=%s", source_node, exc)

    payload: dict = {
        "success": True,
        "room": room_key,
        "sourceNode": source_node,
        "sourceRoom": source_room,
        "sessionId": session_id,
        "bytes": nbytes,
        "filename": stored_name,
        "text": None,
    }

    logger.info("[STT] room=%s transcribing ...", room_key)
    try:
        text = transcribe_audio(dest)
    except STTUnavailableError as exc:
        logger.error("[STT] unavailable room=%s err=%s", room_key, exc)
        emit(
            "SYSTEM_ERROR",
            source_node=source_node,
            source_room=source_room,
            session_id=session_id,
            result="stt_unavailable",
            metadata={"message": str(exc)},
        )
        payload["success"] = False
        payload["error"] = "stt_unavailable"
        payload["message"] = str(exc)
        return payload
    except STTInvalidAudioError as exc:
        logger.error("[STT] invalid audio room=%s err=%s", room_key, exc)
        emit(
            "SYSTEM_ERROR",
            source_node=source_node,
            source_room=source_room,
            session_id=session_id,
            result="invalid_audio",
            metadata={"message": str(exc)},
        )
        payload["success"] = False
        payload["error"] = "invalid_audio"
        payload["message"] = str(exc)
        return payload
    except STTTranscriptionError as exc:
        logger.error("[STT] transcription failed room=%s err=%s", room_key, exc)
        emit(
            "SYSTEM_ERROR",
            source_node=source_node,
            source_room=source_room,
            session_id=session_id,
            result="stt_failed",
            metadata={"message": str(exc)},
        )
        payload["success"] = False
        payload["error"] = "stt_failed"
        payload["message"] = str(exc)
        return payload
    except STTError as exc:
        logger.error("[STT] error room=%s err=%s", room_key, exc)
        emit(
            "SYSTEM_ERROR",
            source_node=source_node,
            source_room=source_room,
            session_id=session_id,
            result="stt_error",
            metadata={"message": str(exc)},
        )
        payload["success"] = False
        payload["error"] = "stt_error"
        payload["message"] = str(exc)
        return payload

    stt_ms = round((time.perf_counter() - t0) * 1000, 1)
    payload["text"] = text
    payload["intent"] = None
    payload["reply"] = None
    payload["message"] = "ok"

    emit(
        "STT_RESULT",
        source_node=source_node,
        source_room=source_room,
        session_id=session_id,
        text=text,
        response_node=source_node,
        result="OK",
        latency_ms=stt_ms,
        metadata={"pathRoom": room_key},
    )

    try:
        voiced = process_voice_command(
            room_key,
            text,
            source_node=source_node,
            source_room_label=source_room,
            session_id=session_id,
        )
        payload["intent"] = voiced.get("intent")
        payload["reply"] = voiced.get("reply")
        payload["target_room"] = voiced.get("target_room")
        payload["query"] = voiced.get("query")
        payload["targetNode"] = voiced.get("target_node")
    except Exception as exc:  # noqa: BLE001 — no tumbar el endpoint de audio
        logger.error("[VOICE] process failed room=%s err=%s", room_key, exc)
        emit(
            "SYSTEM_ERROR",
            source_node=source_node,
            source_room=source_room,
            session_id=session_id,
            text=text,
            result="intent_processing_failed",
            metadata={"message": str(exc)},
        )
        payload["message"] = "transcribed; intent processing failed"

    return payload
