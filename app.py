from __future__ import annotations

from io import BytesIO
import logging
import unicodedata
from typing import Literal

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from kokoro import KPipeline
from pydantic import BaseModel, Field

from system_folders import (
    close_folder,
    create_folder,
    get_last_folder,
    open_folder,
    open_known_folder,
)
from apps import open_app
from window_controller import enter_viernes_compact, restore_viernes_window
from youtube import search_and_play, pause_playback
from audio import save_room_audio
from reply_audio import resolve_reply_wav, store_reply_wav

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="Viernes Core", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Viernes: voz femenina española Dora (Kokoro lang_code=e).
VOICE = "ef_dora"
SAMPLE_RATE = 24_000
pipeline = KPipeline(lang_code="e")

SystemIntent = Literal[
    "SYSTEM_CREATE_FOLDER",
    "SYSTEM_OPEN_FOLDER",
    "SYSTEM_CLOSE_FOLDER",
    "SYSTEM_UI_COMPACT",
    "SYSTEM_UI_RESTORE",
    "SYSTEM_OPEN_APP",
    "OPEN_FOLDER",
    "OPEN_PROJECT",
]


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class ActionRequest(BaseModel):
    intent: SystemIntent
    folderName: str | None = Field(default=None, max_length=120)
    folderKey: str | None = Field(default=None, max_length=64)
    appId: str | None = Field(default=None, max_length=64)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower().strip())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return " ".join(normalized.split())


def resolve_response(text: str) -> str:
    command = normalize_text(text)

    if "hola viernes" in command or command == "viernes":
        return "Bienvenido, señor."

    if "estas despierta" in command or "estas despierto" in command:
        return "Para usted, siempre, señor."

    return "No tengo una respuesta definida para eso todavía, señor."


def generate_wav(text: str, speed: float) -> bytes:
    chunks: list[np.ndarray] = []
    try:
        for _, _, audio in pipeline(text, voice=VOICE, speed=speed):
            chunks.append(np.asarray(audio, dtype=np.float32))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {exc}") from exc

    if not chunks:
        raise HTTPException(status_code=500, detail="Kokoro returned no audio")

    audio = np.concatenate(chunks)
    wav = BytesIO()
    sf.write(wav, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return wav.getvalue()


@app.get("/health")
def health() -> dict[str, str | None]:
    return {
        "status": "ok",
        "tts": "kokoro",
        "voice": VOICE,
        "lastFolder": get_last_folder(),
    }


@app.post("/tts")
def synthesize(request: SpeakRequest) -> Response:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    return Response(
        content=generate_wav(text, request.speed),
        media_type="audio/wav",
        headers={"X-Viernes-Voice": VOICE},
    )


@app.post("/respond")
def respond(request: CommandRequest) -> dict[str, str]:
    reply = resolve_response(request.text)
    return {"input": request.text, "reply": reply}


@app.post("/respond/tts")
def respond_tts(request: CommandRequest) -> Response:
    reply = resolve_response(request.text)
    return Response(
        content=generate_wav(reply, request.speed),
        media_type="audio/wav",
        headers={"X-Viernes-Voice": VOICE},
    )


@app.post("/actions")
def execute_action(request: ActionRequest) -> dict:
    """Allowlist de acciones OS. La UI envía intent + folderName/folderKey opcionales."""
    folder_name = request.folderName.strip() if request.folderName else None
    if folder_name == "":
        folder_name = None

    folder_key = request.folderKey.strip().lower() if request.folderKey else None
    if folder_key == "":
        folder_key = None

    if request.intent == "SYSTEM_CREATE_FOLDER":
        if not folder_name:
            return {
                "success": False,
                "message": "No entendí el nombre de la carpeta.",
            }
        try:
            return create_folder(folder_name)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except OSError as exc:
            logging.getLogger("viernes.system").error("create_folder OSError: %s", exc)
            return {"success": False, "message": "No pude crear la carpeta."}

    if request.intent in {"OPEN_FOLDER", "OPEN_PROJECT"}:
        key = folder_key or (folder_name.lower() if folder_name else None)
        if not key:
            return {
                "success": False,
                "message": "No entendí qué carpeta o proyecto abrir.",
            }
        try:
            return open_known_folder(key)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except OSError as exc:
            logging.getLogger("viernes.system").error("open_known_folder OSError: %s", exc)
            return {"success": False, "message": "No pude abrir la carpeta."}

    if request.intent == "SYSTEM_OPEN_FOLDER":
        try:
            return open_folder(folder_name)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except OSError as exc:
            logging.getLogger("viernes.system").error("open_folder OSError: %s", exc)
            return {"success": False, "message": "No pude abrir la carpeta."}

    if request.intent == "SYSTEM_CLOSE_FOLDER":
        try:
            return close_folder(folder_name)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except OSError as exc:
            logging.getLogger("viernes.system").error("close_folder OSError: %s", exc)
            return {"success": False, "message": "No pude cerrar la carpeta."}

    if request.intent == "SYSTEM_UI_COMPACT":
        return enter_viernes_compact()

    if request.intent == "SYSTEM_UI_RESTORE":
        return restore_viernes_window()

    if request.intent == "SYSTEM_OPEN_APP":
        app_id = request.appId.strip() if request.appId else ""
        if not app_id:
            return {"success": False, "message": "No entendí qué aplicación abrir."}
        try:
            return open_app(app_id)
        except OSError as exc:
            logging.getLogger("viernes.apps").error("open_app OSError: %s", exc)
            return {"success": False, "message": "No pude abrir la aplicación."}

    return {"success": False, "message": "Acción no permitida."}


@app.get("/rooms/{room}/youtube")
def room_youtube(
    room: str,
    q: str = Query(..., min_length=1, max_length=200),
) -> dict:
    """V0: buscar en YouTube (core) y reproducir en el nodo de la habitación."""
    return search_and_play(room, q)


@app.get("/rooms/{room}/pause")
def room_pause(room: str) -> dict:
    """V0: pausar reproducción multimedia en el nodo de la habitación."""
    return pause_playback(room)


@app.post("/rooms/{room}/audio")
async def room_audio(
    room: str,
    file: UploadFile = File(...),
) -> dict:
    """V0: recibir audio del nodo + STT + intent + TTS reply (URL LAN)."""
    try:
        result = await save_room_audio(room, file)
    except ValueError as exc:
        message = str(exc)
        status = 404 if "desconocida" in message.casefold() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except OSError as exc:
        logging.getLogger("viernes.audio").error("save_room_audio OSError: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="No pude guardar el audio temporalmente.",
        ) from exc

    result.setdefault("reply_audio_url", None)
    reply = result.get("reply")
    if isinstance(reply, str) and reply.strip():
        try:
            wav = generate_wav(reply.strip(), 1.0)
            _filename, url = store_reply_wav(wav)
            result["reply_audio_url"] = url
            logging.getLogger("viernes.reply_audio").info(
                "[TTS] room=%s reply_audio_url=%s",
                result.get("room"),
                url,
            )
        except HTTPException as exc:
            logging.getLogger("viernes.reply_audio").error(
                "[TTS] generate failed room=%s detail=%s",
                result.get("room"),
                exc.detail,
            )
        except OSError as exc:
            logging.getLogger("viernes.reply_audio").error(
                "[TTS] store failed room=%s err=%s",
                result.get("room"),
                exc,
            )

    return result


@app.get("/tts/tmp/{filename}")
def serve_reply_tts(filename: str) -> FileResponse:
    """Sirve solo WAV de reply generados por Viernes (allowlist de nombre)."""
    path = resolve_reply_wav(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Audio de respuesta no encontrado.")
    return FileResponse(
        path=path,
        media_type="audio/wav",
        filename=path.name,
    )
