# Observability + Node Registry + Control Panel V0

## Objetivo

Que Viernes pueda observar su sistema distribuido **con datos reales** antes
de Brain V0:

- qué nodos existen (identidad estable)
- si están online
- quién escuchó
- qué se entendió
- qué intent se resolvió
- dónde se respondió

## Separación de conceptos

| Campo | Significado |
| --- | --- |
| `nodeId` | Identidad estable (`desktop-main`, `aqua`, `nova`, …) |
| `room` | Ubicación lógica (`sala`, `baño`, `dormitorio`) |
| `endpoint` | Transporte actual (IP:puerto). Puede cambiar con DHCP |
| `sourceNode` | Quién escuchó |
| `targetNode` | Quién ejecuta |
| `responseNode` | Dónde se responde |

`dónde hablo ≠ dónde se ejecuta ≠ dónde se responde`.

## Dónde vive cada pieza

| Pieza | Repo |
| --- | --- |
| Registry + poll `/health` | `viernes-core` |
| Telemetry ring buffer | `viernes-core` |
| `pendingUiAction` | `viernes-core` |
| Control Panel (workspace `control`) | `viernes-ui` |
| Heartbeat + eventos Desktop | `viernes-ui` → Core |
| Pipeline Aqua/Nova | sin cambios de lifecycle; solo hooks de telemetría en Core |

`viernes-node` no se modifica en esta fase.

## Endpoints Core

| Método | Path | Rol |
| --- | --- | --- |
| `GET` | `/nodes` | lista registry |
| `GET` | `/nodes/{nodeId}` | detalle |
| `POST` | `/nodes/desktop-main/heartbeat` | presencia Desktop |
| `GET` | `/telemetry/events` | timeline (`source`, `errors`, `limit`) |
| `POST` | `/telemetry/events` | Desktop reporta eventos reales |
| `GET` | `/telemetry/last-voice` | último ciclo de voz |
| `GET` | `/ui/pending?consume=true` | Desktop toma acción UI |
| `POST` | `/ui/pending` | encolar Home/Control |
| `GET` | `/system/metrics` | CPU/RAM/GPU (+ `uptimeS`) |

## Eventos

Solo se emiten hechos demostrables. No se inventa `VOICE_DETECTED` si la
cadena no tiene detección explícita de inicio de voz.

Ejemplos reales hoy:

- Aqua/Nova: `STT_RESULT` → `INTENT_RESOLVED` → `ACTION_*` → `TTS_*`
- Desktop (Web Speech): empieza en `STT_RESULT` cuando hay transcript final
- Poll: `NODE_ONLINE` / `NODE_OFFLINE` en transición

## UI

- `Viernes, aparece` → workspace **home**
- `Viernes, panel de control` → workspace **control**

Home no muestra logs. El panel consume Core; sin mocks en producción.

## Seed V0

- `desktop-main` — sala — caps: audio, display, system
- `aqua` — baño — aliases de path: `bano`, `aqua`
- `nova` — dormitorio — path: `nova`

Futuros nodos pueden tener solo camera/sensor/actuator/display.
