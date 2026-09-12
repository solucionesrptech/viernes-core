"""Registro centralizado de habitaciones / nodos Android.

V0: IP fija por habitación. Más adelante se puede reemplazar por
descubrimiento o registro dinámico sin tocar el resto del código.
"""

from __future__ import annotations

import os

# room_id -> base URL del nodo (sin path final).
# Override por env: VIERNES_ROOM_<ROOM> (ej. VIERNES_ROOM_BANO).
_DEFAULT_ROOMS: dict[str, str] = {
    # P20 (alias lógico futuro: aqua). No renombrar: el nodo vivo publica room=bano.
    "bano": "http://192.168.1.96:8080",
    # Override real: VIERNES_ROOM_DORMITORIO=http://IP:8080
    "dormitorio": "http://192.168.1.97:8080",
    # Redmi 9 (device=redmi9). Override: VIERNES_ROOM_NOVA
    "nova": "http://192.168.1.135:8080",
}


def _env_room_url(room: str) -> str | None:
    key = f"VIERNES_ROOM_{room.strip().upper()}"
    raw = os.environ.get(key, "").strip().rstrip("/")
    return raw or None


def get_room_base_url(room: str) -> str:
    """Devuelve la base URL del nodo o lanza ValueError si la room no existe."""
    key = room.strip().casefold()
    if not key:
        raise ValueError("Habitación vacía.")

    configured = _env_room_url(key) or _DEFAULT_ROOMS.get(key)
    if configured is None:
        raise ValueError(f"Habitación desconocida: {key}")

    return configured.rstrip("/")


def list_rooms() -> dict[str, str]:
    """Mapa room -> base URL (env override aplicado)."""
    rooms = dict(_DEFAULT_ROOMS)
    for room in list(rooms):
        override = _env_room_url(room)
        if override:
            rooms[room] = override
    return {k: v.rstrip("/") for k, v in rooms.items()}


def room_exists(room: str) -> bool:
    """True si la habitación está registrada (allowlist V0)."""
    try:
        get_room_base_url(room)
        return True
    except ValueError:
        return False
