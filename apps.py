"""Whitelist de aplicaciones que Viernes puede abrir (sin shell arbitrario)."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("viernes.apps")

APP_LABELS: dict[str, str] = {
    "valorant": "Valorant",
    "cursor": "Cursor",
    "chatgpt": "ChatGPT",
    "chatgpt classic": "ChatGPT Classic",
    "codex": "Codex",
}

# AUMIDs observados en este equipo (Start Menu). Sobreescribibles por env.
DEFAULT_AUMIDS: dict[str, str] = {
    "chatgpt": "OpenAI.Codex_2p2nqsd0c76g0!App",
    "chatgpt classic": "OpenAI.ChatGPT-Desktop_2p2nqsd0c76g0!ChatGPT",
    "codex": "OpenAI.Codex_2p2nqsd0c76g0!App",
}


def _env_path(*keys: str) -> Path | None:
    for key in keys:
        raw = os.environ.get(key, "").strip().strip('"')
        if not raw:
            continue
        path = Path(raw)
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


def _env_aumid(*keys: str) -> str | None:
    for key in keys:
        raw = os.environ.get(key, "").strip().strip('"')
        if raw and "!" in raw and "\\" not in raw and "/" not in raw:
            return raw
    return None


def _first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _launch_executable(path: Path, args: list[str] | None = None) -> None:
    command = [str(path), *(args or [])]
    logger.info("[Apps] Launch exe: %s", command)
    subprocess.Popen(
        command,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _launch_aumid(aumid: str) -> None:
    """Lanza una app empaquetada por AUMID fijo (nunca texto de voz)."""
    # explorer.exe + shell:AppsFolder\<AUMID> — argumentos fijos de whitelist.
    target = f"shell:AppsFolder\\{aumid}"
    logger.info("[Apps] Launch AUMID: %s", aumid)
    subprocess.Popen(
        ["explorer.exe", target],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _not_found(app_id: str) -> dict:
    label = APP_LABELS.get(app_id, app_id)
    return {
        "success": False,
        "appId": app_id,
        "message": f"No pude encontrar {label} en este equipo.",
    }


def _opened(app_id: str, detail: str | None = None) -> dict:
    label = APP_LABELS.get(app_id, app_id)
    payload: dict = {
        "success": True,
        "appId": app_id,
        "message": f"Abriendo {label}.",
    }
    if detail:
        payload["path"] = detail
    return payload


def resolve_riot_client_path() -> Path | None:
    configured = _env_path("VIERNES_VALORANT_PATH", "VIERNES_RIOT_CLIENT_PATH")
    if configured:
        return configured

    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    local_app = os.environ.get("LOCALAPPDATA", "")

    candidates = [
        Path(r"C:\Riot Games\Riot Client\RiotClientServices.exe"),
        Path(program_files) / "Riot Games" / "Riot Client" / "RiotClientServices.exe",
        Path(program_files_x86) / "Riot Games" / "Riot Client" / "RiotClientServices.exe",
    ]
    if local_app:
        candidates.append(
            Path(local_app) / "Riot Games" / "Riot Client" / "RiotClientServices.exe"
        )
    return _first_existing(candidates)


def resolve_cursor_path() -> Path | None:
    configured = _env_path("VIERNES_CURSOR_PATH")
    if configured:
        return configured

    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(program_files) / "cursor" / "Cursor.exe",
        Path(program_files) / "Cursor" / "Cursor.exe",
    ]
    if local_app:
        candidates.extend(
            [
                Path(local_app) / "Programs" / "cursor" / "Cursor.exe",
                Path(local_app) / "Programs" / "Cursor" / "Cursor.exe",
            ]
        )
    return _first_existing(candidates)


def open_valorant() -> dict:
    client = resolve_riot_client_path()
    if client is None:
        return _not_found("valorant")
    try:
        _launch_executable(
            client,
            ["--launch-product=valorant", "--launch-patchline=live"],
        )
    except OSError as exc:
        logger.error("[Apps] open_valorant falló: %s", exc)
        return _not_found("valorant")
    return _opened("valorant", str(client))


def open_cursor() -> dict:
    path = resolve_cursor_path()
    if path is None:
        # Fallback AUMID de Start Menu si existe.
        aumid = _env_aumid("VIERNES_CURSOR_AUMID") or "Anysphere.Cursor"
        try:
            _launch_aumid(aumid)
            return _opened("cursor", aumid)
        except OSError as exc:
            logger.error("[Apps] open_cursor AUMID falló: %s", exc)
            return _not_found("cursor")
    try:
        _launch_executable(path)
    except OSError as exc:
        logger.error("[Apps] open_cursor falló: %s", exc)
        return _not_found("cursor")
    return _opened("cursor", str(path))


def open_chatgpt() -> dict:
    aumid = _env_aumid("VIERNES_CHATGPT_AUMID") or DEFAULT_AUMIDS["chatgpt"]
    exe = _env_path("VIERNES_CHATGPT_PATH")
    try:
        if exe:
            _launch_executable(exe)
            return _opened("chatgpt", str(exe))
        _launch_aumid(aumid)
        return _opened("chatgpt", aumid)
    except OSError as exc:
        logger.error("[Apps] open_chatgpt falló: %s", exc)
        return _not_found("chatgpt")


def open_chatgpt_classic() -> dict:
    aumid = (
        _env_aumid("VIERNES_CHATGPT_CLASSIC_AUMID")
        or DEFAULT_AUMIDS["chatgpt classic"]
    )
    exe = _env_path("VIERNES_CHATGPT_CLASSIC_PATH")
    try:
        if exe:
            _launch_executable(exe)
            return _opened("chatgpt classic", str(exe))
        _launch_aumid(aumid)
        return _opened("chatgpt classic", aumid)
    except OSError as exc:
        logger.error("[Apps] open_chatgpt_classic falló: %s", exc)
        return _not_found("chatgpt classic")


def open_codex() -> dict:
    aumid = _env_aumid("VIERNES_CODEX_AUMID") or DEFAULT_AUMIDS["codex"]
    exe = _env_path("VIERNES_CODEX_PATH")
    try:
        if exe:
            _launch_executable(exe)
            return _opened("codex", str(exe))
        _launch_aumid(aumid)
        return _opened("codex", aumid)
    except OSError as exc:
        logger.error("[Apps] open_codex falló: %s", exc)
        return _not_found("codex")


ALLOWED_APPS: dict[str, Callable[[], dict]] = {
    "valorant": open_valorant,
    "cursor": open_cursor,
    "chatgpt": open_chatgpt,
    "chatgpt classic": open_chatgpt_classic,
    "codex": open_codex,
}


def open_app(app_id: str) -> dict:
    key = " ".join(app_id.strip().casefold().split())
    handler = ALLOWED_APPS.get(key)
    if handler is None:
        return {
            "success": False,
            "appId": key,
            "message": "Aplicación no permitida.",
        }
    return handler()
