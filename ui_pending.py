"""Cola mínima de acciones de UI para el agente Desktop (sesión interactiva)."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_pending: dict[str, Any] | None = None

# Acciones conocidas V0.
UI_SHOW_HOME = "SHOW_HOME"
UI_SHOW_CONTROL = "SHOW_CONTROL"


def set_pending(
    action: str,
    *,
    source_node: str | None = None,
    source_room: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _pending
    item = {
        "action": action,
        "ts": time.time(),
        "sourceNode": source_node,
        "sourceRoom": source_room,
        "metadata": metadata or {},
    }
    with _lock:
        _pending = item
    return item


def peek_pending() -> dict[str, Any] | None:
    with _lock:
        return dict(_pending) if _pending else None


def take_pending() -> dict[str, Any] | None:
    global _pending
    with _lock:
        item = _pending
        _pending = None
        return dict(item) if item else None
