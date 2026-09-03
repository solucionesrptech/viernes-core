# viernes-core

Core local de Viernes (Python).

## Primera capability: voz TTS

Viernes usa Kokoro como sintetizador local y la voz femenina `af_bella` (Bella / American Female).

### Instalar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Kokoro puede requerir `espeak-ng` para el procesamiento de texto/fonemas según el entorno. En Windows, instálalo si Kokoro reporta que no encuentra eSpeak.

### Ejecutar

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

### Probar

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8000/tts `
  -Method POST `
  -ContentType 'application/json' `
  -Body '{"text":"Hola jefe, despierta y lista que vamos a construir hoy","speed":1.0}' `
  -OutFile viernes.wav

Start-Process .\viernes.wav
```

Endpoint de estado: `GET /health`.

La UI local puede consumir `POST /tts` y reproducir directamente el WAV devuelto. La capability de voz queda independiente de cámara/hand tracking.
