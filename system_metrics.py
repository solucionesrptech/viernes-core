"""Recursos de la PC: CPU, RAM y VRAM para decidir si cabe un modelo local."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time

import psutil

logger = logging.getLogger("viernes.metrics")

# nvidia-smi responde en ~60 ms y da VRAM. Medido contra la alternativa previa
# (systeminformation, en StreamDesk): ~2100 ms por consulta y sin VRAM.
_SMI_QUERY = "memory.used,memory.total,temperature.gpu,utilization.gpu"
_SMI_TIMEOUT_S = 3.0
_CACHE_TTL_S = 2.0

_cache: tuple[float, dict] | None = None
_cpu_primed = False


def _read_gpu() -> dict | None:
    """Lee la GPU NVIDIA. Sin nvidia-smi devuelve None: es degradación, no error."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None

    try:
        completed = subprocess.run(
            [smi, f"--query-gpu={_SMI_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_SMI_TIMEOUT_S,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error("nvidia-smi no respondió: %s", exc)
        return None

    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    parts = [piece.strip() for piece in line.split(",")]
    if len(parts) != 4:
        logger.error("nvidia-smi devolvió un formato inesperado: %r", line)
        return None

    try:
        used, total, temp, util = (int(float(piece)) for piece in parts)
    except ValueError as exc:
        logger.error("nvidia-smi devolvió valores no numéricos (%r): %s", line, exc)
        return None

    return {
        "vramUsedMb": used,
        "vramTotalMb": total,
        "vramFreeMb": max(0, total - used),
        "tempC": temp,
        "utilPct": util,
    }


def _read_cpu_pct() -> float:
    """cpu_percent mide contra la llamada anterior: la primera necesita muestra."""
    global _cpu_primed

    if _cpu_primed:
        return psutil.cpu_percent(interval=None)
    _cpu_primed = True
    return psutil.cpu_percent(interval=0.1)


def _read_host() -> dict:
    memory = psutil.virtual_memory()
    mib = 1024 * 1024
    return {
        "cpuPct": round(_read_cpu_pct(), 1),
        "ramUsedMb": (memory.total - memory.available) // mib,
        "ramFreeMb": memory.available // mib,
        "ramTotalMb": memory.total // mib,
    }


def read_metrics() -> dict:
    """Instantánea de recursos, cacheada para que sondearla no cueste nada."""
    global _cache

    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_S:
        return _cache[1]

    gpu = _read_gpu()
    snapshot = {"host": _read_host(), "gpu": gpu, "ts": time.time()}
    _cache = (now, snapshot)
    return snapshot


def vram_free_mb() -> int | None:
    """VRAM libre. None cuando no hay GPU NVIDIA legible."""
    gpu = read_metrics()["gpu"]
    return gpu["vramFreeMb"] if gpu else None
