"""Telemetry / Event Model V0 — buffer en memoria de eventos demostrables."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any

# Tipos V0. Solo emitir cuando el hecho sea real en esa cadena.
EVENT_TYPES = frozenset(
    {
        "NODE_ONLINE",
        "NODE_OFFLINE",
        "HEARTBEAT",
        "VOICE_DETECTED",
        "STT_RESULT",
        "INTENT_RESOLVED",
        "ACTION_STARTED",
        "ACTION_COMPLETED",
        "ACTION_FAILED",
        "TTS_STARTED",
        "TTS_COMPLETED",
        "SYSTEM_WARNING",
        "SYSTEM_ERROR",
        "UI_ACTION",
    }
)

_MAX_EVENTS = 400
_lock = threading.RLock()
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_last_voice: dict[str, Any] | None = None


def _now_ts() -> float:
    return time.time()


def emit(
    event_type: str,
    *,
    source_node: str | None = None,
    source_room: str | None = None,
    session_id: str | None = None,
    text: str | None = None,
    intent: str | None = None,
    target_node: str | None = None,
    response_node: str | None = None,
    result: str | None = None,
    latency_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registra un evento. Tipos desconocidos se guardan igual (extensible)."""
    event: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now_ts(),
        "eventType": event_type,
        "sourceNode": source_node,
        "sourceRoom": source_room,
        "sessionId": session_id,
        "text": text,
        "intent": intent,
        "targetNode": target_node,
        "responseNode": response_node,
        "result": result,
        "latencyMs": latency_ms,
        "metadata": metadata or {},
    }
    with _lock:
        _events.append(event)
        if event_type in {
            "STT_RESULT",
            "INTENT_RESOLVED",
            "ACTION_COMPLETED",
            "ACTION_FAILED",
            "TTS_COMPLETED",
        }:
            _update_last_voice(event)
    return event


def _update_last_voice(event: dict[str, Any]) -> None:
    """Acumula el último ciclo de voz visible en el panel."""
    global _last_voice
    base = dict(_last_voice) if _last_voice else {}
    base["updatedAt"] = event["ts"]
    if event.get("sourceNode"):
        base["sourceNode"] = event["sourceNode"]
    if event.get("sourceRoom"):
        base["sourceRoom"] = event["sourceRoom"]
    if event.get("sessionId"):
        base["sessionId"] = event["sessionId"]
    if event.get("text") is not None:
        base["text"] = event["text"]
    if event.get("intent") is not None:
        base["intent"] = event["intent"]
    if event.get("targetNode") is not None:
        base["targetNode"] = event["targetNode"]
    if event.get("responseNode") is not None:
        base["responseNode"] = event["responseNode"]
    if event.get("result") is not None:
        base["result"] = event["result"]
    if event.get("latencyMs") is not None:
        base["latencyMs"] = event["latencyMs"]
    et = event["eventType"]
    base["lastEventType"] = et
    _last_voice = base


def list_events(
    *,
    source: str | None = None,
    errors_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with _lock:
        items = list(_events)

    source_key = (source or "").strip().casefold()
    if source_key and source_key not in {"all", "*"}:
        if source_key == "core":
            items = [
                e
                for e in items
                if (e.get("sourceNode") or "").casefold() in {"", "core"}
                or (e.get("targetNode") or "").casefold() == "core"
                or e.get("eventType", "").startswith(("INTENT_", "ACTION_", "SYSTEM_"))
            ]
        elif source_key == "errors":
            errors_only = True
        else:
            items = [
                e
                for e in items
                if (e.get("sourceNode") or "").casefold() == source_key
                or (e.get("responseNode") or "").casefold() == source_key
            ]

    if errors_only or source_key == "errors":
        items = [
            e
            for e in items
            if e.get("eventType") in {"SYSTEM_ERROR", "ACTION_FAILED", "SYSTEM_WARNING"}
            or (e.get("result") or "").upper() in {"FAIL", "FAILED", "ERROR"}
        ]

    if limit < 1:
        limit = 1
    return items[-limit:]


def last_voice() -> dict[str, Any] | None:
    with _lock:
        return dict(_last_voice) if _last_voice else None


def ingest_external(payload: dict[str, Any]) -> dict[str, Any]:
    """Acepta un evento reportado por Desktop u otro cliente."""
    event_type = str(payload.get("eventType") or "").strip()
    if not event_type:
        raise ValueError("eventType requerido")
    return emit(
        event_type,
        source_node=payload.get("sourceNode"),
        source_room=payload.get("sourceRoom"),
        session_id=payload.get("sessionId"),
        text=payload.get("text"),
        intent=payload.get("intent"),
        target_node=payload.get("targetNode"),
        response_node=payload.get("responseNode"),
        result=payload.get("result"),
        latency_ms=payload.get("latencyMs"),
        metadata=payload.get("metadata")
        if isinstance(payload.get("metadata"), dict)
        else None,
    )


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]
