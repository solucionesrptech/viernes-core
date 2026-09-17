"""STT local V0 (faster-whisper). Sin rooms ni intents."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

logger = logging.getLogger("viernes.stt")

# V0: small en CPU/int8. Override: VIERNES_STT_MODEL / DEVICE / COMPUTE / TIMEOUT.
# Medido sobre audio real de sala: `base` confunde "qué hora es" con "que ahora
# es" y el intent no dispara; `small` con beam 5 acierta 6 de 7 por +1.8 s.
_DEFAULT_MODEL = "small"
_DEFAULT_DEVICE = "cpu"
_DEFAULT_COMPUTE = "int8"
_DEFAULT_BEAM = 5
_DEFAULT_TIMEOUT_S = 90.0

_model = None
_model_lock_error: BaseException | None = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viernes-stt")


class STTError(Exception):
    """Error base de STT."""


class STTUnavailableError(STTError):
    """Motor STT no disponible (import/modelo)."""


class STTInvalidAudioError(STTError):
    """Audio vacío o formato no legible."""


class STTTranscriptionError(STTError):
    """Fallo o timeout al transcribir."""


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _get_model():
    """Carga perezosa del modelo (una vez por proceso)."""
    global _model, _model_lock_error

    if _model is not None:
        return _model
    if _model_lock_error is not None:
        raise STTUnavailableError(
            f"STT no disponible: {_model_lock_error}"
        ) from _model_lock_error

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        _model_lock_error = exc
        raise STTUnavailableError(
            "STT no disponible: instala faster-whisper (y ffmpeg en PATH para .m4a)."
        ) from exc

    model_name = _env("VIERNES_STT_MODEL", _DEFAULT_MODEL)
    device = _env("VIERNES_STT_DEVICE", _DEFAULT_DEVICE)
    compute = _env("VIERNES_STT_COMPUTE", _DEFAULT_COMPUTE)

    try:
        logger.info(
            "[STT] loading model=%s device=%s compute=%s",
            model_name,
            device,
            compute,
        )
        _model = WhisperModel(model_name, device=device, compute_type=compute)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de carga = no disponible
        _model_lock_error = exc
        raise STTUnavailableError(f"STT no disponible: {exc}") from exc

    return _model


def _transcribe_sync(path: Path) -> str:
    model = _get_model()
    try:
        segments, _info = model.transcribe(
            str(path),
            language="es",
            beam_size=int(_env("VIERNES_STT_BEAM", str(_DEFAULT_BEAM))),
            vad_filter=True,
            initial_prompt="Viernes es el nombre del asistente. Comandos de voz en español.",
        )
        parts: list[str] = []
        for segment in segments:
            piece = (segment.text or "").strip()
            if piece:
                parts.append(piece)
    except STTError:
        raise
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if any(
            token in msg
            for token in ("invalid", "format", "codec", "demux", "open", "averror", "no such")
        ):
            raise STTInvalidAudioError(f"Formato de audio inválido: {exc}") from exc
        raise STTTranscriptionError(f"Fallo de transcripción: {exc}") from exc

    return " ".join(parts).strip()


def transcribe_audio(path: str | Path) -> str:
    """Transcribe un archivo de audio local. Devuelve texto (puede ser vacío)."""
    audio_path = Path(path)
    if not audio_path.is_file():
        raise STTInvalidAudioError("Archivo de audio inexistente.")
    if audio_path.stat().st_size <= 0:
        raise STTInvalidAudioError("Audio vacío.")

    timeout_s = float(_env("VIERNES_STT_TIMEOUT", str(_DEFAULT_TIMEOUT_S)))
    started = time.perf_counter()
    logger.info("[STT] transcribing path=%s", audio_path.name)

    future = _executor.submit(_transcribe_sync, audio_path)
    try:
        text = future.result(timeout=timeout_s)
    except FuturesTimeout as exc:
        future.cancel()
        elapsed = time.perf_counter() - started
        logger.error("[STT] timeout elapsed=%.2fs", elapsed)
        raise STTTranscriptionError(
            f"Timeout de transcripción ({timeout_s:.0f}s)"
        ) from exc
    except STTError:
        elapsed = time.perf_counter() - started
        logger.error("[STT] failed elapsed=%.2fs", elapsed)
        raise

    elapsed = time.perf_counter() - started
    logger.info('[STT] text=%r', text)
    logger.info("[STT] elapsed=%.2fs", elapsed)
    return text
