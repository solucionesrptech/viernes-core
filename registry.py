"""Node Registry V0 — identidad estable de nodos, independiente de IP."""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from rooms import get_room_base_url, list_rooms
from telemetry import emit

logger = logging.getLogger("viernes.registry")

# nodeId estable. endpoint se resuelve por transporte (rooms / heartbeat).
# capabilities son genéricas: no asumir audio en todos los nodos.
_SEED: list[dict[str, Any]] = [
    {
        "nodeId": "desktop-main",
        "displayName": "Desktop",
        "deviceType": "desktop",
        "room": "sala",
        "endpointKey": None,
        "endpoint": None,
        "capabilities": ["audio_input", "audio_output", "display", "system"],
        "pathAliases": ["desktop", "desktop-main"],
        "metadata": {"role": "desktop_agent"},
    },
    {
        "nodeId": "aqua",
        "displayName": "Aqua",
        "deviceType": "android_phone",
        "room": "baño",
        "endpointKey": "bano",
        "endpoint": None,
        "capabilities": ["audio_input", "audio_output"],
        "pathAliases": ["bano", "aqua"],
        "metadata": {"legacyRoomPath": "bano"},
    },
    {
        "nodeId": "nova",
        "displayName": "Nova",
        "deviceType": "android_phone",
        "room": "dormitorio",
        "endpointKey": "nova",
        "endpoint": None,
        "capabilities": ["audio_input", "audio_output"],
        "pathAliases": ["nova"],
        "metadata": {},
    },
]

_OFFLINE_AFTER_S = 45.0
_POLL_INTERVAL_S = 10.0
_HEALTH_TIMEOUT_S = 3.0

_lock = threading.RLock()
_nodes: dict[str, dict[str, Any]] = {}
_alias_to_node: dict[str, str] = {}
_poll_thread: threading.Thread | None = None
_poll_stop = threading.Event()
_started_at = time.time()


def _resolve_endpoint(seed: dict[str, Any]) -> str | None:
    key = seed.get("endpointKey")
    if not key:
        return seed.get("endpoint")
    try:
        return get_room_base_url(str(key))
    except ValueError:
        return seed.get("endpoint")


def _blank_runtime() -> dict[str, Any]:
    return {
        "online": False,
        "lastSeen": None,
        "health": None,
        "reportedEndpoint": None,
    }


def init_registry() -> None:
    """Carga seed V0. Idempotente."""
    with _lock:
        if _nodes:
            return
        for seed in _SEED:
            node_id = seed["nodeId"]
            entry = {
                "nodeId": node_id,
                "displayName": seed["displayName"],
                "deviceType": seed["deviceType"],
                "room": seed["room"],
                "endpointKey": seed.get("endpointKey"),
                "endpoint": _resolve_endpoint(seed),
                "capabilities": list(seed.get("capabilities") or []),
                "pathAliases": list(seed.get("pathAliases") or []),
                "metadata": dict(seed.get("metadata") or {}),
                **_blank_runtime(),
            }
            _nodes[node_id] = entry
            for alias in entry["pathAliases"]:
                _alias_to_node[alias.casefold()] = node_id
            _alias_to_node[node_id.casefold()] = node_id
        logger.info("[REGISTRY] seeded nodes=%s", list(_nodes))


def resolve_node_id(path_or_id: str) -> str | None:
    """Mapea path de audio / alias → nodeId estable."""
    key = (path_or_id or "").strip().casefold()
    if not key:
        return None
    with _lock:
        return _alias_to_node.get(key)


def resolve_room_for_path(path_or_id: str) -> str | None:
    node_id = resolve_node_id(path_or_id)
    if not node_id:
        return None
    with _lock:
        node = _nodes.get(node_id)
        return node.get("room") if node else None


def list_nodes() -> list[dict[str, Any]]:
    init_registry()
    now = time.time()
    with _lock:
        out = []
        for node in _nodes.values():
            item = dict(node)
            # Refresh endpoint from rooms map (DHCP / env override).
            item["endpoint"] = _resolve_endpoint(node) or node.get("reportedEndpoint")
            last = item.get("lastSeen")
            if last is not None and (now - float(last)) > _OFFLINE_AFTER_S:
                item["online"] = False
            out.append(item)
        return out


def get_node(node_id: str) -> dict[str, Any] | None:
    resolved = resolve_node_id(node_id) or (node_id or "").strip().casefold()
    for node in list_nodes():
        if node["nodeId"] == resolved:
            return node
    return None


def mark_seen(
    node_id: str,
    *,
    health: dict[str, Any] | None = None,
    endpoint: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Actualiza presencia. Emite NODE_ONLINE solo en transición."""
    init_registry()
    resolved = resolve_node_id(node_id) or (node_id or "").strip().casefold()
    with _lock:
        node = _nodes.get(resolved)
        if node is None:
            return None
        was_online = bool(node.get("online"))
        node["online"] = True
        node["lastSeen"] = time.time()
        if health is not None:
            node["health"] = health
        if endpoint:
            node["reportedEndpoint"] = endpoint.rstrip("/")
            if not node.get("endpointKey"):
                node["endpoint"] = node["reportedEndpoint"]
        if metadata:
            merged = dict(node.get("metadata") or {})
            merged.update(metadata)
            node["metadata"] = merged
        snapshot = dict(node)
    if not was_online:
        emit(
            "NODE_ONLINE",
            source_node=resolved,
            source_room=snapshot.get("room"),
            result="ONLINE",
            metadata={"endpoint": snapshot.get("endpoint")},
        )
    return snapshot


def mark_offline(node_id: str, *, reason: str = "unreachable") -> None:
    init_registry()
    resolved = resolve_node_id(node_id) or (node_id or "").strip().casefold()
    with _lock:
        node = _nodes.get(resolved)
        if node is None:
            return
        was_online = bool(node.get("online"))
        node["online"] = False
        room = node.get("room")
    if was_online:
        emit(
            "NODE_OFFLINE",
            source_node=resolved,
            source_room=room,
            result="OFFLINE",
            metadata={"reason": reason},
        )


def record_desktop_heartbeat(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    health = {
        "listening": data.get("listening"),
        "workspace": data.get("workspace"),
        "windowMode": data.get("windowMode"),
        "ua": data.get("ua"),
    }
    # Quitar Nones para no fingir datos.
    health = {k: v for k, v in health.items() if v is not None}
    snap = mark_seen(
        "desktop-main",
        health=health or None,
        endpoint=data.get("endpoint"),
        metadata={"lastHeartbeatSource": "desktop"},
    )
    return snap or {}


def _fetch_node_health(endpoint: str) -> dict[str, Any] | None:
    url = f"{endpoint.rstrip('/')}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        import json

        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.debug("[REGISTRY] health fail endpoint=%s err=%s", endpoint, exc)
        return None


def poll_remote_nodes_once() -> None:
    """Consulta /health de nodos con endpoint. No toca el código del nodo."""
    init_registry()
    with _lock:
        targets = [
            (n["nodeId"], _resolve_endpoint(n))
            for n in _nodes.values()
            if n.get("endpointKey") or n.get("endpoint")
        ]
    for node_id, endpoint in targets:
        if not endpoint:
            continue
        health = _fetch_node_health(endpoint)
        if health is None:
            mark_offline(node_id, reason="health_unreachable")
            continue
        # Solo campos reales del /health del nodo.
        slim = {
            k: health[k]
            for k in (
                "status",
                "listening",
                "heartbeatAgeS",
                "backend",
                "uptimeS",
                "device",
                "name",
                "room",
                "pid",
            )
            if k in health
        }
        mark_seen(node_id, health=slim, endpoint=endpoint)


def _poll_loop() -> None:
    while not _poll_stop.wait(_POLL_INTERVAL_S):
        try:
            poll_remote_nodes_once()
        except Exception as exc:  # noqa: BLE001 — el poll no tumba el Core
            logger.error("[REGISTRY] poll error: %s", exc)


def start_background_poll() -> None:
    global _poll_thread
    init_registry()
    if _poll_thread and _poll_thread.is_alive():
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(
        target=_poll_loop,
        name="viernes-registry-poll",
        daemon=True,
    )
    _poll_thread.start()
    # Primera pasada inmediata.
    try:
        poll_remote_nodes_once()
    except Exception as exc:  # noqa: BLE001
        logger.error("[REGISTRY] initial poll error: %s", exc)
    logger.info("[REGISTRY] background poll started interval=%ss", _POLL_INTERVAL_S)


def stop_background_poll() -> None:
    _poll_stop.set()


def core_uptime_s() -> float:
    return round(time.time() - _started_at, 1)


def rooms_transport_map() -> dict[str, str]:
    """Expuesto para diagnóstico: room path → endpoint (no es identidad)."""
    return list_rooms()
