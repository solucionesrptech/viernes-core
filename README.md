# viernes-core

Core local de Viernes: FastAPI + Kokoro (TTS) + faster-whisper (STT). Recibe el
audio que los nodos de cada habitación detectan, resuelve la intención y
devuelve la respuesta hablada.

## Entorno

El código vive en `D:\Desarrollos\Viernes\viernes-core`, pero **el entorno
virtual vive en el SSD**, no junto al código:

```text
C:\Users\VIERNES\.venvs\viernes-core
```

`D:` es un disco mecánico y el entorno pesa 1,25 GB, con 502 MB solo de
`torch`. Con el entorno en `D:`, el primer arranque tras un reinicio tardaba
6,1 minutos, de los cuales 4 min 23 s eran importar librerías desde el disco
lento. Desde el SSD son 2,5 minutos. No devolver el entorno a `D:`.

### Recrearlo desde cero

```powershell
$base = "C:\Users\VIERNES\AppData\Local\Python\pythoncore-3.12-64\python.exe"
$venv = "C:\Users\VIERNES\.venvs\viernes-core"

& $base -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r D:\Desarrollos\Viernes\viernes-core\requirements.txt
```

Python 3.12. Los modelos se descargan solos a `C:\Users\VIERNES\.cache\huggingface`
la primera vez, así que recrear el entorno no obliga a bajarlos de nuevo.
Kokoro puede pedir `espeak-ng` según el entorno; instalarlo solo si lo reporta.

Después de recrear el entorno hay que **volver a registrar la tarea**, porque
apunta al ejecutable por ruta absoluta.

## Autoarranque

Lo gestiona la tarea programada de Windows `Viernes Core`. No usa PM2 ni ningún
proceso supervisor residente.

| Aspecto | Valor |
| --- | --- |
| Ejecuta | `C:\Users\VIERNES\.venvs\viernes-core\Scripts\pythonw.exe run_core.py` |
| Directorio | `D:\Desarrollos\Viernes\viernes-core` |
| Tipo de inicio | `S4U`, privilegios limitados |
| Disparador 1 | Arranque del sistema, con 30 s de retardo |
| Disparador 2 | Repetición cada 2 min (watchdog) |
| Instancias | `IgnoreNew` |

`S4U` ("Service For User") permite arrancar **sin que nadie inicie sesión y sin
almacenar la contraseña de la cuenta**. Es la razón por la que se descartó
`AutoAdminLogon`, que obliga a escribirla en el registro.

Consecuencia: el Core corre en la **sesión 0**, sin escritorio. Toda la cadena
de voz es de red y no se ve afectada. Lo que no funciona desde ahí es
`/actions` (abrir apps y carpetas, control de ventanas Win32). Es una capacidad
degradada declarada; ver la separación pendiente del agente de escritorio en
las reglas de arquitectura del workspace.

Medido tras un reinicio real sin iniciar sesión: marca de arranque a los 66 s,
escuchando en el puerto a los 150 s.

### Registrar o reparar la tarea

Requiere **una** elevación. Si se registra sin elevar, Windows la degrada a
`Interactive` y se pierde el arranque sin login.

```powershell
$core = "D:\Desarrollos\Viernes\viernes-core"
$pyw  = "C:\Users\VIERNES\.venvs\viernes-core\Scripts\pythonw.exe"

$action = New-ScheduledTaskAction -Execute $pyw -Argument "run_core.py" -WorkingDirectory $core

$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = "PT30S"
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 2)

$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" `
    -LogonType S4U -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName "Viernes Core" -Action $action -Trigger @($boot, $watch) `
    -Principal $principal -Settings $settings -Force
```

## Watchdog

El reinicio por fallo del Programador de tareas **no sirve** para esto: solo
actúa cuando la tarea no consigue arrancar, no cuando el proceso muere. Se
comprobó matando el Core y la tarea quedó en `Ready` sin revivirlo.

El watchdog real es el disparador repetitivo: cada 2 minutos intenta lanzar la
tarea y `MultipleInstances=IgnoreNew` descarta el intento si el Core ya está
vivo. Si murió, lo levanta. Verificado matando el proceso: revivió solo, sin
duplicarse y sin errores de puerto ocupado.

## Estado y logs

`GET /health` responde cuando el Core acepta peticiones. Escucha en
`0.0.0.0:8000`, así que los nodos lo alcanzan por la IP LAN de la PC.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Carga Kokoro y Whisper al iniciar, de modo que tarda en aceptar la primera
petición: **esperar a `/health` antes de dar el arranque por fallido**.

`GET /system/metrics` devuelve CPU y RAM (`psutil`) y GPU y VRAM
(`nvidia-smi`), cacheado 2 s. La VRAM libre es el dato que decide si cabe un
modelo local.

Los logs van a `logs/core.log`, rotado a 2 MB por 3 copias. Es la **única**
salida disponible: bajo la tarea el Core corre con `pythonw`, que no tiene
consola.

## Ejecución manual

Para desarrollo, sin pasar por la tarea:

```powershell
cd D:\Desarrollos\Viernes\viernes-core
C:\Users\VIERNES\.venvs\viernes-core\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Detener antes la tarea para no chocar en el puerto 8000.

## Recuperación

1. **No responde.** Comprobar que el proceso vive y en qué sesión:
   `Get-Process pythonw | Select-Object Id, SessionId`. La sesión debe ser 0.
   Si no hay proceso, esperar un ciclo del watchdog (2 min) antes de intervenir.
2. **Ni el watchdog lo levanta.** Revisar `logs/core.log` y el estado de la
   tarea con `Get-ScheduledTaskInfo -TaskName "Viernes Core"`.
3. **La tarea perdió S4U** (`Get-ScheduledTask` muestra `Interactive`): volver a
   registrarla elevada con el bloque de arriba.
4. **El entorno se corrompió:** recrearlo con los pasos de la sección Entorno y
   volver a registrar la tarea.

## Voz

Kokoro con la voz femenina española Dora (`ef_dora`, `lang_code="e"`).

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8000/tts -Method POST `
  -ContentType 'application/json' `
  -Body '{"text":"Hola jefe","speed":1.0}' -OutFile viernes.wav
```
