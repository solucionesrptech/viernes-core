"""Parser mínimo de intenciones en Core (V0 Rooms).

Intents V0:
  - TIME_GET_CURRENT
  - ROOM_MUSIC_PLAY   (pon <query> [en el baño|dormitorio])
  - ROOM_MUSIC_PAUSE  (pausa/para [la música] [en el baño|dormitorio])

Exige wake word Viernes. Si no hay habitación en la frase, usa source_room.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from registry import resolve_node_id
from telemetry import emit
from ui_pending import UI_SHOW_CONTROL, UI_SHOW_HOME, set_pending
from youtube import pause_playback, search_and_play

logger = logging.getLogger("viernes.intents")

TIME_HINTS = (
    "que hora es",
    "que ahora es",  # variante STT frecuente de "qué hora es"
    "dime la hora",
    "dame la hora",
    "me das la hora",
    "cual es la hora",
)

# Tras normalize_transcript, "baño" -> "bano".
_KNOWN_ROOMS = {
    "bano": "bano",
    "dormitorio": "dormitorio",
}

_ROOM_LABEL = {
    "bano": "baño",
    "dormitorio": "dormitorio",
}

# Hardcode V0: "pon rancheras ..." → búsqueda fija Pedro Infante.
_V0_QUERY_OVERRIDE = {
    "rancheras": "Pedro Infante rancheras",
}


def normalize_transcript(value: str) -> str:
    """Minúsculas, sin tildes, sin puntuación ruidosa."""
    normalized = unicodedata.normalize("NFD", value.lower().strip())
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = re.sub(r"[^a-zñ0-9\s_-]", " ", normalized)
    return " ".join(normalized.split())


def has_wake_word(normalized: str) -> bool:
    return bool(
        re.search(r"\bviernes\b", normalized)
        or re.search(r"\bvienes\b", normalized)
        or re.search(r"\bbienes\b", normalized)
    )


def format_current_time_reply(now: datetime | None = None) -> str:
    """Hora local del sistema (equivalente práctico a es-CL en este PC)."""
    moment = now or datetime.now()
    return f"Son las {moment.strftime('%H:%M')}"


def match_command(normalized: str) -> dict[str, Any] | None:
    """Devuelve {intent, target_room?, query?} o None. Exige wake word."""
    if not has_wake_word(normalized):
        return None

    # Panel de control / Home (UI Desktop vía pendingUiAction).
    if re.search(r"\bpanel\s+de\s+control\b", normalized):
        return {
            "intent": "UI_SHOW_CONTROL",
            "target_room": None,
            "query": None,
        }
    if (
        re.search(r"\b(aparece|muestrate|mostrate)\b", normalized)
        or (
            re.search(r"\bvuelve\b", normalized)
            and not re.search(r"\btamano\b", normalized)
        )
    ):
        return {
            "intent": "UI_SHOW_HOME",
            "target_room": None,
            "query": None,
        }

    # Pausa con habitación: "pausa/para la música en el baño"
    pause_room = re.search(
        r"\b(?:pausa|para)(?:\s+la\s+musica)?\s+en\s+(?:el\s+)?(bano|dormitorio)\b",
        normalized,
    )
    if pause_room:
        room = _KNOWN_ROOMS.get(pause_room.group(1))
        if room:
            return {
                "intent": "ROOM_MUSIC_PAUSE",
                "target_room": room,
                "query": None,
            }

    # Pausa local (sin habitación): "pausa la música" / "para la música"
    if re.search(r"\b(?:pausa|para)\s+la\s+musica\b", normalized):
        return {
            "intent": "ROOM_MUSIC_PAUSE",
            "target_room": None,
            "query": None,
        }

    # Play con habitación
    play_room = re.search(
        r"\b(?:pon|ponme|poneme|reproduce(?:\s+me)?|reproduceme)\s+(.+?)\s+en\s+(?:el\s+)?"
        r"(bano|dormitorio)\b",
        normalized,
    )
    if play_room:
        query = (play_room.group(1) or "").strip()
        room = _KNOWN_ROOMS.get(play_room.group(2))
        if query and room:
            query = _V0_QUERY_OVERRIDE.get(query, query)
            return {
                "intent": "ROOM_MUSIC_PLAY",
                "target_room": room,
                "query": query,
            }

    # Play local (sin habitación): "pon rancheras"
    play_local = re.search(
        r"\b(?:pon|ponme|poneme|reproduce(?:\s+me)?|reproduceme)\s+(.+)$",
        normalized,
    )
    if play_local:
        query = (play_local.group(1) or "").strip()
        if query and not re.search(
            r"\ben\s+(?:el\s+)?(bano|dormitorio)\b", query
        ):
            query = _V0_QUERY_OVERRIDE.get(query, query)
            return {
                "intent": "ROOM_MUSIC_PLAY",
                "target_room": None,
                "query": query,
            }

    if any(hint in normalized for hint in TIME_HINTS):
        return {"intent": "TIME_GET_CURRENT", "target_room": None, "query": None}

    if "la hora" in normalized and any(
        token in normalized for token in ("dime", "dame", "que", "cual")
    ):
        return {"intent": "TIME_GET_CURRENT", "target_room": None, "query": None}

    return None


def execute_command(
    matched: dict[str, Any],
    *,
    source_node: str | None = None,
    source_room_label: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Ejecuta el intent y devuelve reply + metadata de acción."""
    intent = matched.get("intent")
    target_room = matched.get("target_room")
    query = matched.get("query")

    out: dict[str, Any] = {
        "intent": intent,
        "target_room": target_room,
        "query": query,
        "reply": None,
        "action_ok": False,
        "target_node": "core",
    }

    if intent == "UI_SHOW_CONTROL":
        set_pending(
            UI_SHOW_CONTROL,
            source_node=source_node,
            source_room=source_room_label,
        )
        emit(
            "UI_ACTION",
            source_node=source_node,
            source_room=source_room_label,
            session_id=session_id,
            intent=intent,
            target_node="desktop-main",
            response_node="desktop-main",
            result="QUEUED",
            metadata={"action": UI_SHOW_CONTROL},
        )
        out["reply"] = "Abriendo el panel de control."
        out["action_ok"] = True
        out["target_node"] = "desktop-main"
        return out

    if intent == "UI_SHOW_HOME":
        set_pending(
            UI_SHOW_HOME,
            source_node=source_node,
            source_room=source_room_label,
        )
        emit(
            "UI_ACTION",
            source_node=source_node,
            source_room=source_room_label,
            session_id=session_id,
            intent=intent,
            target_node="desktop-main",
            response_node="desktop-main",
            result="QUEUED",
            metadata={"action": UI_SHOW_HOME},
        )
        out["reply"] = "Aquí estoy."
        out["action_ok"] = True
        out["target_node"] = "desktop-main"
        return out

    if intent == "TIME_GET_CURRENT":
        out["reply"] = format_current_time_reply()
        out["action_ok"] = True
        return out

    if intent == "ROOM_MUSIC_PLAY":
        room = str(target_room or "")
        q = str(query or "").strip()
        label = _ROOM_LABEL.get(room, room)
        target_node = resolve_node_id(room) or room
        out["target_node"] = target_node
        result = search_and_play(room, q)
        out["action_result"] = result
        if result.get("success"):
            title = result.get("title") or q
            out["reply"] = f"Reproduciendo {title} en el {label}."
            out["action_ok"] = True
        else:
            out["reply"] = result.get("message") or f"No pude reproducir en el {label}."
        return out

    if intent == "ROOM_MUSIC_PAUSE":
        room = str(target_room or "")
        label = _ROOM_LABEL.get(room, room)
        target_node = resolve_node_id(room) or room
        out["target_node"] = target_node
        result = pause_playback(room)
        out["action_result"] = result
        if result.get("success"):
            out["reply"] = f"Música en pausa en el {label}."
            out["action_ok"] = True
        else:
            out["reply"] = result.get("message") or f"No pude pausar en el {label}."
        return out

    return out


def process_voice_command(
    room: str,
    text: str,
    *,
    source_node: str | None = None,
    source_room_label: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Procesa texto STT: wake + intent + acción. Sin acción si no matchea.

    `room` es el path de origen (micrófono). Si el comando no nombra
    habitación, el destino es la de origen.
    """
    source_path = room.strip().casefold()
    node_id = source_node or resolve_node_id(source_path) or source_path
    room_label = source_room_label or source_path
    raw = (text or "").strip()
    logger.info("[VOICE] room=%s node=%s text=%r", source_path, node_id, raw)

    result: dict[str, Any] = {
        "intent": None,
        "reply": None,
        "matched": False,
        "source_room": source_path,
        "source_node": node_id,
        "target_room": None,
        "target_node": None,
        "query": None,
    }

    if not raw:
        logger.info("[INTENT] room=%s intent=None (empty text)", source_path)
        return result

    normalized = normalize_transcript(raw)
    if not has_wake_word(normalized):
        logger.info("[INTENT] room=%s intent=None (no wake)", source_path)
        return result

    matched = match_command(normalized)
    if matched is None:
        logger.info("[INTENT] room=%s intent=None (no match)", source_path)
        return result

    intent = matched["intent"]
    target_room = matched.get("target_room")
    query = matched.get("query")

    if target_room is None and intent in {"ROOM_MUSIC_PLAY", "ROOM_MUSIC_PAUSE"}:
        target_room = source_path
        matched["target_room"] = target_room

    logger.info(
        "[INTENT] room=%s intent=%s target_room=%s query=%r",
        source_path,
        intent,
        target_room,
        query,
    )

    emit(
        "INTENT_RESOLVED",
        source_node=node_id,
        source_room=room_label,
        session_id=session_id,
        text=raw,
        intent=intent,
        target_node="core",
        result="MATCHED",
        metadata={"targetRoom": target_room, "query": query},
    )

    emit(
        "ACTION_STARTED",
        source_node=node_id,
        source_room=room_label,
        session_id=session_id,
        intent=intent,
        target_node="core",
        text=raw,
    )

    executed = execute_command(
        matched,
        source_node=node_id,
        source_room_label=room_label,
        session_id=session_id,
    )
    reply = executed.get("reply")
    action_ok = bool(executed.get("action_ok"))
    target_node = executed.get("target_node") or "core"
    logger.info(
        "[ACTION] room=%s intent=%s target_room=%s result=%r",
        source_path,
        intent,
        target_room,
        reply,
    )

    emit(
        "ACTION_COMPLETED" if action_ok else "ACTION_FAILED",
        source_node=node_id,
        source_room=room_label,
        session_id=session_id,
        text=raw,
        intent=intent,
        target_node=target_node,
        response_node=node_id,
        result="SUCCESS" if action_ok else "FAILED",
        metadata={"reply": reply, "targetRoom": target_room},
    )

    result["intent"] = intent
    result["reply"] = reply
    result["matched"] = True
    result["target_room"] = target_room
    result["target_node"] = target_node
    result["query"] = query
    result["action_ok"] = action_ok
    return result
