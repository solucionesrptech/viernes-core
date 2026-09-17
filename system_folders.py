"""Capabilities de filesystem limitadas a D:\\Viernes + carpetas conocidas (Windows)."""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from window_controller import enter_viernes_compact

logger = logging.getLogger("viernes.system")

BASE_PATH = Path(r"D:\Viernes")
WM_CLOSE = 0x0010

# Carpetas/proyectos conocidos (allowlist). Override: VIERNES_WORKSPACE.
KNOWN_FOLDERS: dict[str, Path] = {
    "viernes": Path(os.environ.get("VIERNES_WORKSPACE", r"D:\Desarrollos\Viernes")),
}

# Estado de sesión en memoria (proceso del core).
_last_folder: str | None = None
# Única ventana Explorer cuya ownership reclamó Viernes (HWND nuevo al abrir).
_managed_explorer_hwnd: int | None = None

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DRIVE_OR_UNC = re.compile(r"^(?:[a-zA-Z]:|\\\\)")
_EXPLORER_CLASSES = frozenset({"CabinetWClass", "ExploreWClass"})


def get_last_folder() -> str | None:
    return _last_folder


def get_managed_explorer_hwnd() -> int | None:
    return _managed_explorer_hwnd


def sanitize_folder_name(folder_name: str) -> str:
    name = folder_name.strip().strip(". ")
    if not name:
        raise ValueError("El nombre de carpeta está vacío.")
    if _DRIVE_OR_UNC.match(name):
        raise ValueError("No se permiten rutas absolutas ni UNC.")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("El nombre no puede contener separadores de ruta.")
    if _FORBIDDEN.search(name):
        raise ValueError("El nombre contiene caracteres no permitidos.")
    if name in {".", ".."}:
        raise ValueError("Nombre de carpeta no válido.")
    return name


def ensure_base_path() -> Path:
    if not BASE_PATH.exists():
        raise ValueError(
            "No existe D:\\Viernes. Crea esa carpeta y concede escritura antes de continuar."
        )
    if not BASE_PATH.is_dir():
        raise ValueError("D:\\Viernes existe pero no es una carpeta.")
    return BASE_PATH.resolve()


def resolve_under_base(folder_name: str) -> Path:
    safe = sanitize_folder_name(folder_name)
    base = ensure_base_path()
    target = (base / safe).resolve()

    try:
        common = os.path.commonpath([str(base), str(target)])
    except ValueError as exc:
        raise ValueError("La ruta queda fuera de D:\\Viernes.") from exc

    if common.lower() != str(base).lower():
        raise ValueError("La ruta queda fuera de D:\\Viernes.")

    if target.parent.resolve() != base:
        raise ValueError("Solo se permiten carpetas directamente bajo D:\\Viernes.")

    return target


def resolve_known_folder(folder_key: str) -> Path:
    key = folder_key.strip().lower()
    configured = KNOWN_FOLDERS.get(key)
    if configured is None:
        raise ValueError("No conozco esa carpeta o proyecto.")

    try:
        target = configured.expanduser().resolve()
    except OSError as exc:
        raise ValueError("No pude resolver la ruta de la carpeta conocida.") from exc

    allowed = {path.expanduser().resolve() for path in KNOWN_FOLDERS.values()}
    if target not in allowed:
        raise ValueError("La ruta queda fuera de la allowlist de carpetas conocidas.")

    return target


def resolve_target(folder_name: str | None) -> Path:
    global _last_folder

    if folder_name:
        return resolve_under_base(folder_name)

    if _last_folder:
        path = Path(_last_folder).resolve()
        # lastFolder puede ser carpeta conocida o bajo D:\Viernes
        for known in KNOWN_FOLDERS.values():
            try:
                if path == known.expanduser().resolve():
                    return path
            except OSError:
                continue
        base = ensure_base_path()
        try:
            common = os.path.commonpath([str(base), str(path)])
        except ValueError as exc:
            raise ValueError("La última carpeta ya no es válida.") from exc
        if common.lower() != str(base).lower():
            raise ValueError("La última carpeta queda fuera de D:\\Viernes.")
        return path

    raise ValueError("No hay una carpeta reciente en esta sesión.")


def create_folder(folder_name: str) -> dict:
    global _last_folder

    target = resolve_under_base(folder_name)

    if target.exists():
        if target.is_dir():
            _last_folder = str(target)
            logger.info("create_folder already_exists path=%s", target)
            return {
                "success": True,
                "path": str(target),
                "alreadyExisted": True,
                "message": "Esta carpeta ya existe.",
            }
        return {
            "success": False,
            "path": str(target),
            "alreadyExisted": False,
            "message": "Ya existe un archivo con ese nombre.",
        }

    try:
        target.mkdir(parents=False)
    except PermissionError:
        logger.error("create_folder permission denied path=%s", target)
        return {
            "success": False,
            "path": str(target),
            "alreadyExisted": False,
            "message": "No tengo permiso para crear carpetas en D:\\Viernes.",
        }
    except FileExistsError:
        if target.is_dir():
            _last_folder = str(target)
            return {
                "success": True,
                "path": str(target),
                "alreadyExisted": True,
                "message": "Esta carpeta ya existe.",
            }
        return {
            "success": False,
            "path": str(target),
            "alreadyExisted": False,
            "message": "Ya existe un archivo con ese nombre.",
        }

    _last_folder = str(target)
    logger.info("create_folder created path=%s", target)
    return {
        "success": True,
        "path": str(target),
        "alreadyExisted": False,
        "message": "Carpeta creada.",
    }


def _snapshot_explorer_hwnds() -> set[int]:
    user32 = ctypes.windll.user32
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        classname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, classname, 256)
        if classname.value in _EXPLORER_CLASSES:
            found.add(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return found


def _window_title(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_class(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    classname = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, classname, 256)
    return classname.value


def _wait_for_new_explorer_hwnd(before: set[int], timeout_s: float = 2.5) -> set[int]:
    """Espera brevemente a que Explorer publique un HWND nuevo."""
    deadline = time.monotonic() + timeout_s
    latest_new: set[int] = set()
    while time.monotonic() < deadline:
        after = _snapshot_explorer_hwnds()
        latest_new = after - before
        if latest_new:
            # Dar un instante extra por si aparecen ventanas en cascada.
            time.sleep(0.25)
            after = _snapshot_explorer_hwnds()
            latest_new = after - before
            return latest_new
        time.sleep(0.15)
    return latest_new


def _open_explorer_path(target: Path) -> dict:
    global _last_folder, _managed_explorer_hwnd

    if not target.is_dir():
        return {
            "success": False,
            "path": str(target),
            "message": "No encontré esa carpeta.",
        }

    before = _snapshot_explorer_hwnds()
    logger.info("[SystemFolders] Explorer HWND before: %s", sorted(before))

    # /n, fuerza ventana nueva cuando Explorer lo permite (mejor tracking de HWND).
    subprocess.run(
        ["explorer.exe", "/n,", str(target)],
        check=False,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    new_hwnds = _wait_for_new_explorer_hwnd(before)
    after = _snapshot_explorer_hwnds()
    logger.info("[SystemFolders] Explorer HWND after: %s", sorted(after))
    logger.info("[SystemFolders] Explorer HWND new: %s", sorted(new_hwnds))

    hwnd_tracked = False
    if len(new_hwnds) == 1:
        _managed_explorer_hwnd = next(iter(new_hwnds))
        hwnd_tracked = True
        logger.info(
            "[SystemFolders] Managed HWND: %s (nueva ventana; title=%r class=%s)",
            _managed_explorer_hwnd,
            _window_title(_managed_explorer_hwnd),
            _window_class(_managed_explorer_hwnd),
        )
    else:
        logger.warning(
            "[SystemFolders] Managed HWND: None (new_count=%s). "
            "Explorer reutilizó ventana o el HWND no es inequívoco; no se asumirá cierre.",
            len(new_hwnds),
        )

    _last_folder = str(target)
    message = "Carpeta abierta."
    if not hwnd_tracked:
        message = (
            "Carpeta abierta. Explorer reutilizó una ventana existente; "
            "no registré ownership para cerrarla después."
        )

    minimize_result = enter_viernes_compact()

    return {
        "success": True,
        "path": str(target),
        "hwndTracked": hwnd_tracked,
        "managedHwnd": _managed_explorer_hwnd if hwnd_tracked else None,
        "compactUi": True,
        "viernesCompact": bool(minimize_result.get("compact")),
        "viernesMinimized": False,
        "viernesHwnd": minimize_result.get("hwnd"),
        "message": message,
    }


def open_folder(folder_name: str | None = None) -> dict:
    try:
        target = resolve_target(folder_name)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    return _open_explorer_path(target)


def open_known_folder(folder_key: str) -> dict:
    """Abre una carpeta/proyecto de la allowlist (p. ej. workspace Viernes)."""
    try:
        target = resolve_known_folder(folder_key)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    logger.info("[SystemFolders] open_known_folder key=%s path=%s", folder_key, target)
    return _open_explorer_path(target)


def close_folder(folder_name: str | None = None) -> dict:
    """Cierra solo el HWND Explorer gestionado por Viernes (nunca taskkill)."""
    global _managed_explorer_hwnd

    # folder_name / lastFolder se usan solo para mensajes; el cierre va por HWND.
    target_path: str | None = None
    try:
        target_path = str(resolve_target(folder_name))
    except ValueError:
        target_path = _last_folder

    hwnd = _managed_explorer_hwnd
    user32 = ctypes.windll.user32

    logger.info("[SystemFolders] Closing managed HWND: %s", hwnd)

    if hwnd is None:
        return {
            "success": False,
            "path": target_path,
            "message": (
                "No tengo registrada una ventana del Explorador abierta por mí."
            ),
        }

    is_window = bool(user32.IsWindow(hwnd))
    classname = _window_class(hwnd) if is_window else ""
    title = _window_title(hwnd) if is_window else ""
    logger.info("[SystemFolders] IsWindow: %s", is_window)
    logger.info("[SystemFolders] ClassName: %s", classname)
    logger.info("[SystemFolders] Title: %r", title)

    if not is_window:
        _managed_explorer_hwnd = None
        return {
            "success": False,
            "path": target_path,
            "message": "La ventana del Explorador ya no está abierta.",
        }

    if classname not in _EXPLORER_CLASSES:
        _managed_explorer_hwnd = None
        return {
            "success": False,
            "path": target_path,
            "message": "La ventana registrada ya no es del Explorador.",
        }

    ok = bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))
    closed_hwnd = hwnd
    _managed_explorer_hwnd = None
    logger.info(
        "[SystemFolders] PostMessage WM_CLOSE hwnd=%s ok=%s",
        closed_hwnd,
        ok,
    )

    if not ok:
        return {
            "success": False,
            "path": target_path,
            "managedHwnd": closed_hwnd,
            "message": "No pude cerrar la ventana del Explorador.",
        }

    return {
        "success": True,
        "path": target_path,
        "managedHwnd": closed_hwnd,
        "message": "Carpeta cerrada.",
    }
