from io import BytesIO

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from kokoro import KPipeline
from pydantic import BaseModel, Field

app = FastAPI(title="Viernes Core", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Viernes: voz femenina Bella (American English).
VOICE = "af_bella"
SAMPLE_RATE = 24_000
pipeline = KPipeline(lang_code="a")


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "tts": "kokoro", "voice": VOICE}


@app.post("/tts")
def synthesize(request: SpeakRequest) -> Response:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    chunks: list[np.ndarray] = []
    try:
        for _, _, audio in pipeline(text, voice=VOICE, speed=request.speed):
            chunks.append(np.asarray(audio, dtype=np.float32))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {exc}") from exc

    if not chunks:
        raise HTTPException(status_code=500, detail="Kokoro returned no audio")

    audio = np.concatenate(chunks)
    wav = BytesIO()
    sf.write(wav, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")

    return Response(
        content=wav.getvalue(),
        media_type="audio/wav",
        headers={"X-Viernes-Voice": VOICE},
    )
