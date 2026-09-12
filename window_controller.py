"""Control Win32 de la ventana del host de Viernes (Chrome/Vite).

Modo NORMAL ↔ COMPACT (ventana pequeña visible con avatar).
No usa SW_MINIMIZE para no suspender SpeechRecognition del navegador.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

logger = logging.getLogger("viernes.window")

user32 = ctypes.WinDLL("user32", use_last_error=True)

SW_RESTORE = 9
SW_SHOW = 5
HWND_TOP = wintypes.HWND(0)
HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_SHOWWINDOW = 0x0040
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002

COMPACT_WIDTH = 300
COMPACT_HEIGHT = 340
COMPACT_MARGIN = 16

_VIERNES_NAME = "viernes"
_VIERNES_MARKERS = ("ai core", "localhost:5173", "localhost:5174", "localhost:517")


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int


_managed_viernes_hwnd: int | None = None
_saved_rect: RECT | None = None
_compact_active = False


def get_managed_viernes_hwnd() -> int | None:
    return _managed_viernes_hwnd


def is_compact_active() -> bool:
    return _compact_active


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _is_viernes_ui_title(title: str) -> bool:
    lower = title.casefold()
    if _VIERNES_NAME not in lower:
        return False
    return any(marker in lower for marker in _VIERNES_MARKERS)


def find_viernes_window() -> int | None:
    """Devuelve el HWND de la UI de Viernes, o None si no hay coincidencia única."""
    matches: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindow(hwnd):
            return True
        if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            return True
        title = _window_title(int(hwnd))
        if not title:
            return True
        if "viernes" in title.casefold():
            logger.info(
                "[WindowController] Candidate: HWND=%s title=%r",
                int(hwnd),
                title,
            )
        if _is_viernes_ui_title(title):
            matches.append((int(hwnd), title))
        return True

    user32.EnumWindows(callback, 0)

    if len(matches) == 0:
        logger.warning("[WindowController] Viernes HWND=None (sin coincidencias)")
        return None

    if len(matches) > 1:
        logger.warning(
            "[WindowController] Viernes HWND=None (ambiguo: %s)",
            matches,
        )
        return None

    hwnd, title = matches[0]
    logger.info("[WindowController] Viernes HWND=%s title=%r", hwnd, title)
    return hwnd


def enter_viernes_compact() -> dict:
    """Pasa la ventana de Viernes a modo compacto (pequeña, visible, topmost)."""
    global _managed_viernes_hwnd, _saved_rect, _compact_active

    hwnd = find_viernes_window()
    if hwnd is None:
        return {
            "success": False,
            "compact": False,
            "hwnd": None,
            "message": "No pude identificar la ventana de Viernes para compactarla.",
        }

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.2)

    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return {
            "success": False,
            "compact": False,
            "hwnd": hwnd,
            "message": "No pude leer la geometría de la ventana de Viernes.",
        }

    if not _compact_active:
        _saved_rect = RECT(rect.left, rect.top, rect.right, rect.bottom)

    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    x = max(0, int(screen_w - COMPACT_WIDTH - COMPACT_MARGIN))
    y = max(0, int(screen_h - COMPACT_HEIGHT - COMPACT_MARGIN - 48))

    ctypes.set_last_error(0)
    ok = bool(
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            HWND_TOPMOST,
            x,
            y,
            COMPACT_WIDTH,
            COMPACT_HEIGHT,
            SWP_SHOWWINDOW,
        )
    )
    err = ctypes.get_last_error()
    if not ok:
        logger.warning(
            "[WindowController] SetWindowPos compact falló hwnd=%s lastError=%s; reintento TOP",
            hwnd,
            err,
        )
        ctypes.set_last_error(0)
        ok = bool(
            user32.SetWindowPos(
                wintypes.HWND(hwnd),
                HWND_TOP,
                x,
                y,
                COMPACT_WIDTH,
                COMPACT_HEIGHT,
                SWP_SHOWWINDOW,
            )
        )
        err = ctypes.get_last_error()

    user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)
    _managed_viernes_hwnd = hwnd
    _compact_active = bool(ok)
    logger.info(
        "[WindowController] Compact Viernes HWND=%s ok=%s err=%s size=%sx%s pos=%s,%s",
        hwnd,
        ok,
        err,
        COMPACT_WIDTH,
        COMPACT_HEIGHT,
        x,
        y,
    )
    return {
        "success": ok,
        "compact": ok,
        "hwnd": hwnd,
        "message": "Viernes en modo compacto." if ok else "No pude compactar Viernes.",
    }


def restore_viernes_window() -> dict:
    """Restaura tamaño/posición normal, quita topmost y trae foco."""
    global _managed_viernes_hwnd, _saved_rect, _compact_active

    hwnd = _managed_viernes_hwnd
    if hwnd is None or not user32.IsWindow(hwnd):
        hwnd = find_viernes_window()

    if hwnd is None:
        return {
            "success": False,
            "restored": False,
            "hwnd": None,
            "message": "No hay ventana de Viernes para restaurar.",
        }

    handle = wintypes.HWND(hwnd)
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)
        time.sleep(0.15)

    ctypes.set_last_error(0)
    if _saved_rect is not None:
        width = max(200, int(_saved_rect.right - _saved_rect.left))
        height = max(200, int(_saved_rect.bottom - _saved_rect.top))
        ok = bool(
            user32.SetWindowPos(
                handle,
                HWND_NOTOPMOST,
                int(_saved_rect.left),
                int(_saved_rect.top),
                width,
                height,
                SWP_SHOWWINDOW,
            )
        )
    else:
        ok = bool(
            user32.SetWindowPos(
                handle,
                HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                SWP_SHOWWINDOW | SWP_NOSIZE | SWP_NOMOVE,
            )
        )
        user32.ShowWindow(handle, SW_RESTORE)

    err = ctypes.get_last_error()
    if not ok:
        logger.warning(
            "[WindowController] SetWindowPos restore falló hwnd=%s lastError=%s",
            hwnd,
            err,
        )

    user32.ShowWindow(handle, SW_SHOW)
    foreground_ok = bool(user32.SetForegroundWindow(handle))
    user32.BringWindowToTop(handle)

    _managed_viernes_hwnd = hwnd
    _compact_active = False
    success = bool(ok) or foreground_ok
    logger.info(
        "[WindowController] Restored Viernes HWND=%s ok=%s foreground=%s err=%s",
        hwnd,
        ok,
        foreground_ok,
        err,
    )
    return {
        "success": success,
        "restored": success,
        "hwnd": hwnd,
        "message": "Aquí estoy." if success else "No pude restaurar Viernes.",
    }


def minimize_viernes_window() -> dict:
    """Compat: el flujo antiguo pedía minimize; ahora compacta."""
    result = enter_viernes_compact()
    return {
        "success": result.get("success", False),
        "minimized": False,
        "compact": result.get("compact", False),
        "hwnd": result.get("hwnd"),
        "message": result.get("message", ""),
    }
