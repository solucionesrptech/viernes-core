"""Búsqueda YouTube (yt-dlp) y reproducción en nodos de habitación.

No descarga audio/video: solo metadata + GET al nodo Android.
"""

from __future__ import annotations

import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import yt_dlp

from rooms import get_room_base_url

logger = logging.getLogger("viernes.youtube")

NODE_TIMEOUT_S = 8.0
SEARCH_TIMEOUT_S = 25.0
_VIDEO_ID_RE = re.compile(r"^[\w-]{6,64}$")


class YouTubeSearchError(Exception):
    """Fallo al buscar en YouTube vía yt-dlp."""


class YouTubeNoResultsError(Exception):
    """La búsqueda no devolvió entradas."""


class NodeUnavailableError(Exception):
    """El nodo no respondió o rechazó la petición."""


class NodeTimeoutError(Exception):
    """Timeout al contactar el nodo."""


def search_youtube(query: str) -> dict[str, str]:
    """Busca el primer resultado de YouTube sin descargar.

    Returns:
        {"videoId": "...", "title": "..."}
    """
    q = " ".join(query.strip().split())
    if not q:
        raise ValueError("query vacía")

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
        "socket_timeout": SEARCH_TIMEOUT_S,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{q}", download=False)
    except Exception as exc:
        logger.error("[YouTube] yt-dlp search failed query=%r err=%s", q, exc)
        raise YouTubeSearchError(str(exc)) from exc

    entries = (info or {}).get("entries") or []
    entry = next((e for e in entries if e), None)
    if not entry:
        raise YouTubeNoResultsError("Sin resultados")

    video_id = str(entry.get("id") or "").strip()
    title = str(entry.get("title") or "").strip() or "(sin título)"

    if not video_id or not _VIDEO_ID_RE.match(video_id):
        logger.error("[YouTube] videoId inválido: %r", video_id)
        raise YouTubeSearchError("videoId inválido en resultado")

    logger.info("[YouTube] search ok query=%r videoId=%s title=%r", q, video_id, title)
    return {"videoId": video_id, "title": title}


def play_youtube(room: str, video_id: str) -> dict[str, Any]:
    """Envía videoId al nodo de la habitación: GET /youtube?video=<id>."""
    vid = video_id.strip()
    if not _VIDEO_ID_RE.match(vid):
        raise ValueError("videoId inválido")

    try:
        base = get_room_base_url(room)
    except ValueError:
        raise

    url = f"{base}/youtube?{urllib.parse.urlencode({'video': vid})}"
    logger.info("[YouTube] play room=%s url=%s", room, url)

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=NODE_TIMEOUT_S) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read(512)
    except (TimeoutError, socket.timeout) as exc:
        logger.error("[YouTube] node timeout room=%s", room)
        raise NodeTimeoutError("Timeout al contactar el nodo") from exc
    except urllib.error.HTTPError as exc:
        logger.error("[YouTube] node HTTP error room=%s code=%s", room, exc.code)
        raise NodeUnavailableError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(
            reason
        ).lower():
            logger.error("[YouTube] node timeout room=%s reason=%s", room, reason)
            raise NodeTimeoutError("Timeout al contactar el nodo") from exc
        logger.error("[YouTube] node unavailable room=%s err=%s", room, exc)
        raise NodeUnavailableError(str(exc)) from exc

    if status >= 400:
        raise NodeUnavailableError(f"HTTP {status}")

    return {
        "success": True,
        "room": room.strip().casefold(),
        "videoId": vid,
        "nodeStatus": status,
        "nodeBodyPreview": body.decode("utf-8", errors="replace")[:200],
    }


def search_and_play(room: str, query: str) -> dict[str, Any]:
    """Flujo V0: query → búsqueda → play en nodo."""
    room_key = room.strip().casefold()
    q = " ".join(query.strip().split())

    payload: dict[str, Any] = {
        "success": False,
        "room": room_key,
        "query": q,
        "videoId": None,
        "title": None,
    }

    try:
        get_room_base_url(room_key)
    except ValueError as exc:
        payload["error"] = "unknown_room"
        payload["message"] = str(exc)
        return payload

    try:
        found = search_youtube(q)
    except YouTubeNoResultsError:
        payload["error"] = "no_results"
        payload["message"] = "No encontré resultados en YouTube."
        return payload
    except YouTubeSearchError as exc:
        payload["error"] = "search_failed"
        payload["message"] = f"Error de búsqueda YouTube: {exc}"
        return payload
    except ValueError as exc:
        payload["error"] = "invalid_query"
        payload["message"] = str(exc)
        return payload

    payload["videoId"] = found["videoId"]
    payload["title"] = found["title"]

    try:
        play_youtube(room_key, found["videoId"])
    except NodeTimeoutError:
        payload["error"] = "timeout"
        payload["message"] = "El nodo no respondió a tiempo."
        return payload
    except NodeUnavailableError as exc:
        payload["error"] = "node_unavailable"
        payload["message"] = f"Nodo no disponible: {exc}"
        return payload
    except ValueError as exc:
        payload["error"] = "invalid_video"
        payload["message"] = str(exc)
        return payload

    payload["success"] = True
    payload["message"] = "Reproduciendo en el nodo."
    return payload


def pause_playback(room: str) -> dict[str, Any]:
    """Pausa reproducción en el nodo: GET /pause."""
    room_key = room.strip().casefold()
    payload: dict[str, Any] = {
        "success": False,
        "room": room_key,
    }

    try:
        base = get_room_base_url(room_key)
    except ValueError as exc:
        payload["error"] = "unknown_room"
        payload["message"] = str(exc)
        return payload

    url = f"{base}/pause"
    logger.info("[YouTube] pause room=%s url=%s", room_key, url)

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=NODE_TIMEOUT_S) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read(512)
    except (TimeoutError, socket.timeout) as exc:
        logger.error("[YouTube] pause timeout room=%s", room_key)
        payload["error"] = "timeout"
        payload["message"] = "El nodo no respondió a tiempo."
        return payload
    except urllib.error.HTTPError as exc:
        logger.error("[YouTube] pause HTTP error room=%s code=%s", room_key, exc.code)
        payload["error"] = "node_unavailable"
        payload["message"] = f"HTTP {exc.code}"
        return payload
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(
            reason
        ).lower():
            payload["error"] = "timeout"
            payload["message"] = "El nodo no respondió a tiempo."
            return payload
        logger.error("[YouTube] pause unavailable room=%s err=%s", room_key, exc)
        payload["error"] = "node_unavailable"
        payload["message"] = str(exc)
        return payload

    if status >= 400:
        payload["error"] = "node_unavailable"
        payload["message"] = f"HTTP {status}"
        return payload

    payload["success"] = True
    payload["message"] = "Reproducción en pausa."
    payload["nodeStatus"] = status
    payload["nodeBodyPreview"] = body.decode("utf-8", errors="replace")[:200]
    return payload
