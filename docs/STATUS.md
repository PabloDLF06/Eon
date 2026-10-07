# EON — Estado real

Fecha de esta sesión: 2026-10-07 (Europe/Madrid).

## Punto de partida verificado

Antes de modificar nada se ejecutaron `git status`, `git log --oneline -10`,
`git branch -a` y el listado de `C:\Dev\Eon` con elementos ocultos. El repositorio
estaba vacío: solo existía `.git`, no había commits ni archivos de proyecto.
`main` era una referencia sin commit; no existía como rama real.

## Fases e implementación

Fase 0: completada en esta sesión en su alcance documental y estructural.
Fases 1 a 11: pendientes; no se avanza sin confirmación humana explícita.

- Diseño previsto: [SPEC.md](SPEC.md); todas sus capacidades son objetivos.
- Implementación real: ninguna. No hay subsistemas implementados ni un solo
  archivo `.py` en el proyecto, incluido `config.py`.
- Estructura: documentos, configuración declarativa y carpetas con `.gitkeep`.
- `install.bat`: solo comprueba Python >= 3.10, Git y la respuesta HTTP de un
  servicio Ollama ya disponible en `localhost:11434`.
- `start.bat`: solo anuncia que EON está en construcción.
- No se instalan dependencias, no se crea un entorno virtual y no se ejecuta
  Ollama ni se descargan, cargan o invocan modelos.
- No hay contratos de runtime congelados ni pruebas de módulos inexistentes.

Comprobaciones realizadas en esta sesión: inventario exacto de 20 archivos
(13 archivos y 7 `.gitkeep`), ausencia de `.py`, JSON idéntico al solicitado,
lista de 12 fases en el orden y con el texto pedidos, y ejecución de ambos `.bat`.
`install.bat` comprobó Python >= 3.10 y Git y consultó `/api/version` de un servicio
Ollama que ya respondía en localhost; no inició Ollama ni invocó modelos.
Ambos `.bat` finalizaron con código 0. Esto no valida la compatibilidad de las
dependencias con el Python instalado ni ninguna funcionalidad futura de EON.

## Defaults pendientes de validar

Todos los valores de `user_settings.json` son defaults PENDIENTES DE VALIDAR,
incluidos auto-hide, tiempos, idioma, perfil de voz, modelos y límites de gasto.
El modelo `brain` (`qwen3:8b`) se validará mediante A/B en Fase 2 antes de fijar
la decisión; las demás asignaciones tampoco son definitivas.

Las versiones fijadas en `requirements.txt` son propuestas basadas en versiones
publicadas no retiradas en PyPI, consultadas el 2026-10-07. No se ha validado su
compatibilidad conjunta ni instalado nada. Las librerías para TTS Piper y Voice
Fingerprint Lock se decidirán en sus fases correspondientes.

## Git y cierre de sesión

Rama de trabajo: `feature/fase-0-scaffolding`.
Mensaje del commit solicitado: `chore: Fase 0 - scaffolding inicial del proyecto EON`.

Commit inicial de Fase 0: `50752d2`, con el mensaje solicitado. El primer
`git push origin feature/fase-0-scaffolding` terminó correctamente.

Tras ese push, `git ls-remote --symref origin HEAD` y los metadatos de GitHub
confirmaron que la rama por defecto es `feature/fase-0-scaffolding`. Se ha avisado
explícitamente a Pablo: si quiere que `main` sea la rama por defecto más adelante,
deberá decidir su creación y el cambio de rama por defecto. No se ha cambiado
esa configuración ni se ha creado `main`, hecho merge o push a esa rama.

Un segundo commit exclusivamente documental registra este cierre posterior al
primer push, sin reescribir el commit inicial. Al cierre, ambos commits quedan
subidos a la misma rama y el árbol de trabajo está limpio. `.runtime/.gitkeep`
está efectivamente trackeado pese al patrón de exclusión de `.runtime/*`.

## Cierre posterior solicitado por Pablo — 2026-10-07

- A petición de Pablo, se corrigió la identidad de autor y committer de los
  commits de Fase 0 a `PabloDLF06`, manteniendo el correo privado
  `124751066+PabloDLF06@users.noreply.github.com`.
- Se creó `main` y Pablo la fijó manualmente como rama por defecto en GitHub.
  Se confirmó mediante `git ls-remote --symref origin HEAD`, que muestra
  `refs/heads/main`.
- Por decisión explícita de Pablo, los tres commits anteriores de Fase 0 se
  compactaron mediante squash en un único commit raíz:
  `06ca4d31799ca3e9778ee9db9b3efe72d90ac1ee`, con el mensaje
  `chore: Fase 0 - scaffolding inicial del proyecto EON`. La finalidad es
  eliminar cualquier rastro del nombre legal completo del historial Git de
  Fase 0. El árbol de archivos del squash es exactamente el mismo que el de
  `main` antes de la operación; solo se compactó el historial.
- Se eliminó `feature/fase-0-scaffolding` local y remotamente después de confirmar
  que todo su contenido estaba incorporado a `main`. También se retiró la
  referencia local de respaldo `refs/original/refs/heads/feature/fase-0-scaffolding`
  que conservaba el historial anterior.
- Se comprobó la ausencia del nombre legal completo en `git log -p main` y en
  `git log --all -p`. La actualización de `main` usó `--force-with-lease`.
- Este registro añade el commit documental de cierre al commit único de
  scaffolding. Al finalizar, `main` está sincronizada con `origin/main` y el
  árbol de trabajo está limpio. Las entradas anteriores se conservan como
  registro histórico de sesiones previas; este apartado refleja el estado actual.
- No se ha iniciado la Fase 1. Sigue pendiente de confirmación humana explícita.

## Fase 1 completada — 2026-10-07

Pablo autorizó explícitamente esta fase después de revisar el cierre de Fase 0.
Las entradas anteriores describen sesiones previas; este apartado recoge la
implementación actual. El trabajo queda en `feature/fase-1-model-router`, creada
desde `main`, con commit y push de esa rama; no se hace merge a `main` ni se borra
la rama de trabajo.

Archivos nuevos:

- `config.py`: carga única del JSON, validación explícita de los cinco roles
  obligatorios y accesores de configuración. Errores claros en español ante
  archivo ausente, corrupto o valores inválidos, sin defaults inventados.
- `core/model_router.py`: interfaz abstracta `ModelProvider`, proveedor Ollama
  real por HTTP y `ModelRouter` con VRAM Monogamy, serialización, consulta real de
  residentes, carga/descarga verificadas, generación textual, logging y telemetría
  diagnóstica mediante `nvidia-smi`.
- `tests/test_model_router.py`: 62 casos unitarios de configuración, HTTP mockeado,
  concurrencia, residencia y hardware simulado, más un test separado de integración
  real con activación explícita.

Se reemplazó `docs/CONTRACTS.md` con las interfaces implementadas y congeladas y
se añadieron las decisiones de Fase 1 a `docs/DECISIONS.md`. La interfaz común
no podrá modificarse para Fase 9 sin una decisión documentada previa.

Verificación real antes del commit:

```text
Python 3.14.8 · pytest 9.1.1 · requests 2.34.2 · Ollama 0.40.0
pytest tests/test_model_router.py -v -s
EON_RUN_OLLAMA_INTEGRATION=1
63 passed in 7.92s
```

La integración se ejecutó una vez: cargó `nomic-embed-text`, confirmó que
`nomic-embed-text:latest` era el único residente en `/api/ps`, lo descargó con
`keep_alive: 0` y confirmó que el inventario quedaba vacío. La carga usó la
alternativa vacía `/api/embed` porque ese modelo no admite `/api/generate`.
La generación textual y los cambios entre modelos se validaron con HTTP
mockeado; no se afirma una prueba real de generación para todos los modelos.

Snapshots reales de `nvidia-smi`, GPU 0, capacidad total 8188 MiB:

| Momento | VRAM total usada |
| --- | --- |
| Antes de cargar | 8 MiB |
| Con `nomic-embed-text:latest` residente | 421 MiB |
| Después de descargar | 8 MiB |

Durante la residencia, `/api/ps` informó `size_vram=323150151` bytes para el modelo.
Las lecturas de NVIDIA son del consumo total de la GPU, no picos ni medidas de
disco; el valor de `/api/ps` corresponde al dato de residencia comunicado por
Ollama. Son datos puramente informativos, sin decisiones automáticas ni exención
para embeddings. Tras las pruebas, `ollama ps` mostró solo la cabecera, sin
modelos huérfanos, y una lectura posterior de NVIDIA volvió a mostrar 8/8188 MiB.

Los informes completos se conservan localmente en `.runtime/fase-1-pytest-output.txt`
y `.runtime/fase-1-pytest.xml`; están ignorados por Git. Para la prueba se creó
`.venv` e instalaron únicamente requests, pytest y sus dependencias transitivas.
No se instala el resto de `requirements.txt` ni se descargan modelos.

Esta fase NO cubre proveedores de nube, Secrets Vault ni ningún otro componente
de seguridad, GUI, voz, visión, Genesis Engine, memoria o las mejoras posteriores.
Los getters de ajustes no implementan esos subsistemas. No se han modificado
`user_settings.json`, las asignaciones de modelos ni las carpetas de esos módulos.

Pendiente para Fase 2: validación A/B del modelo brain/dispatcher, incluyendo
`qwen3:8b` como candidato provisional y mediciones reales para fijar la decisión.
Fase 2 no se inicia sin confirmación humana explícita de Pablo. El merge de esta
rama tampoco se realiza hasta su validación manual.

## Deuda técnica conocida

### 2026-10-07 — Detección textual de modelos que requieren /api/embed

La detección de modelos que requieren `/api/embed` en vez de `/api/generate`
depende de que Ollama devuelva HTTP 400 y de la coincidencia textual del mensaje
de error `"does not support generate"`. Esta dependencia del texto del servidor
es deuda técnica conocida de Fase 1.

Si una futura versión de Ollama cambia ese texto, no se activará la alternativa
de carga por `/api/embed` y la carga de modelos de embeddings fallará de forma
controlada: se registrará el fallo en logging, no se cargará el modelo y el
proceso principal continuará. Debe revisarse esta detección si se actualiza la
versión de Ollama. Esta auditoría documenta la deuda sin modificar la lógica
funcional ya implementada y probada.

## Fase 1 fusionada a main — 2026-10-07

Tras la confirmación explícita de Pablo y la verificación de cierre de auditoría,
Fase 1 se fusionó a `main` mediante `--no-ff`, con el commit de merge
`781a829a9063f306dc43f875ec6dddb6e0934cc6` y el tag anotado
`fase-1-completa`, ambos publicados en el remoto. Se confirmó que el contenido
del merge coincidía exactamente con el de la rama de fase, y se eliminó
`feature/fase-1-model-router` local y remotamente.

La verificación previa al merge terminó con 62 tests aprobados y el test de
integración real omitido; `requests==2.34.2` y `pytest==9.1.1` en `.venv`
coincidieron con `requirements.txt`. Fase 2 permanece pendiente de una nueva
confirmación explícita de Pablo.

## Fase 2 — Benchmark de brain/dispatcher — 2026-10-07

Por autorización explícita de Pablo, se creó `feature/fase-2-brain-benchmark`
desde `main`, después de confirmar el merge y el tag `fase-1-completa`, el árbol
limpio, los tres candidatos instalados y Ollama local sin modelos residentes.
El alcance es medir y reportar; no fijar automáticamente ningún modelo.

Se añadieron `docs/fase2_brain_dataset.json` (30 casos en español, cinco por
ruta, mostrado en el chat antes de usarlo), `scripts/fase2_brain_benchmark.py`
y el informe generado `docs/fase2_brain_report.md`. Se reutilizaron
`ModelRouter` y `OllamaProvider` reales sin modificar `core/model_router.py`
ni `config.py`. La asignación de `brain` se sustituyó temporalmente en memoria
solo durante el diagnóstico, restaurándola al salir; `user_settings.json`
y `docs/DECISIONS.md` permanecen sin cambios.

La ejecución realizó exactamente 90 llamadas reales, sin reintentos, en
328,750 segundos (5,48 minutos): Qwen obtuvo 29/30 aciertos y 5/5 aclaraciones;
Llama, 25/30 y 3/5; Gemma, 8/30 y 0/5. Hubo 0, 1 y 19 fallos de formato,
respectivamente, y ningún timeout ni fallo técnico de carga o generación.
Los bloques Markdown cuentan como formato inválido; no se repararon las
respuestas para calcular los resultados. Cada modelo se descargó y se confirmó
`ollama ps` vacío y `/api/ps` vacío antes de cargar el siguiente; al final
Ollama quedó sin modelos residentes. Las lecturas de VRAM son snapshots reales
del consumo total de GPU, no picos ni memoria exclusiva del modelo.

Se verificaron offline la validación del dataset, la restauración del getter,
el parseo estricto, el bloqueo ante residentes ajenos y el generador del informe.
Los resultados se recalcularon desde las 90 respuestas originales, con seis
comprobaciones de aislamiento y cinco muestras completas de reasoning por
modelo. Tras la ejecución solo se corrigieron los delimitadores Markdown del
informe, regenerándolo sin inferencias ni alteración de la evidencia. El
informe distingue el hash del script ejecutado del generador posterior.
La suite de Fase 1 terminó con 62 tests aprobados y la integración real omitida.

La evidencia completa y el log quedan locales e ignorados por Git en
`.runtime/fase2_brain_results.json` y `.runtime/fase2_brain_benchmark.log`.
No se descargaron modelos ni se instalaron dependencias. No se hace merge
ni push a `main`. La decisión sobre `brain` sigue pendiente de Pablo y de su
registro explícito posterior en `docs/DECISIONS.md`; no se inicia Fase 3.

## Fase 2 completa: decisión de brain confirmada por Pablo — 2026-10-07

Tras revisar [`fase2_brain_report.md`](fase2_brain_report.md), Pablo confirmó
explícitamente `qwen3:8b` como modelo definitivo de `brain`/dispatcher. La
decisión, sus métricas, el trade-off de latencia aceptado y la posibilidad
de revisar el modo de razonamiento en una fase futura quedan registrados
en la sección 11 de [`DECISIONS.md`](DECISIONS.md), titulada
«Fase 2: qwen3:8b fijado como brain definitivo — 2026-10-07».

`model_assignments.brain` ya tenía `provider: ollama` y `model: qwen3:8b`;
se comprobó y no se modificó `user_settings.json`. No se alteraron el router,
la configuración de producción, el dataset ni el informe, ni se repitieron
inferencias. Fase 2 queda completa tras el benchmark y esta decisión humana.

Pablo autorizó explícitamente el cierre mediante merge `--no-ff` a `main`,
el tag anotado `fase-2-completa` y la eliminación de la rama de fase después
de publicar y verificar ambos. Fase 3 no se inicia: sigue pendiente de una
nueva confirmación explícita de Pablo.

## Fase 3 en curso: integración real fallida — 2026-10-07

Por autorización explícita de Pablo, se trabaja en
`feature/fase-3-voice-engine`, creada desde el `main` limpio en
`744cc8159a64110ebec300c477969a4d2279df79`, que contiene el merge de Fase 2.
Se leyeron completos STATUS.md y CONTRACTS.md antes de escribir código.
`main`, `core/model_router.py`, `safety/`, GUI y visión permanecen sin cambios.
No se ha hecho commit, push ni merge: los cambios de esta fase siguen locales
y el árbol de trabajo NO está limpio.

Se implementaron los tres módulos `core/acoustic_detector.py` (incluye
WakeWordDetector), `core/voice_engine.py` y `core/barge_in.py`, sus tres suites
unitarias y un test separado de integración real. Configuración y contratos
se ampliaron con `get_voice_settings` y las interfaces de voz. Los tests
unitarios utilizan hardware/modelos simulados; no sustituyen la integración.

En el `.venv` existente, Python 3.14.8, se instalaron sounddevice 0.5.6,
faster-whisper 1.2.1, openwakeword 0.6.0, webrtcvad-wheels 2.0.14.post1,
piper-tts 1.8.0 y NumPy 2.5.3; transitivas relevantes: CTranslate2 4.8.2,
ONNX Runtime CPU 1.30.0 y PyAV 19.0.1. Requests 2.34.2 y pytest 9.1.1
conservan sus versiones. Se sustituyó el WebRTC VAD clásico por su distribución
de wheels porque la importación del clásico requería `pkg_resources`.
Piper y NumPy se añadieron pineados. No se instalaron dependencias de fases
posteriores. Las importaciones reales y PortAudio de Windows funcionaron;
`pip check` pasó, pero la compatibilidad funcional conjunta sigue pendiente.
Los motivos, fuentes y detalles se registran en DECISIONS.md, sección 12.

Configuración seleccionada: Whisper `base` en CPU/int8; Piper
`es_ES-davefx-medium`; VAD 2 y 16000 Hz. Wake-word `hey_jarvis` es PROVISIONAL
y en inglés, hasta entrenar/validar un modelo custom «Hey Eon» posteriormente.
Los seis IDs built-in enumerados son alexa, hey_mycroft, hey_jarvis,
hey_rhasspy, timer y weather. Voz CPU-only es una decisión explícita para
preservar VRAM Monogamy sin acoplar todavía estos módulos al router.

Resultados comprobados de esta sesión:

```text
Suite completa sin hardware: 167 passed, 2 skipped in 2.21s
Integración real de voz (una sola ejecución): 1 failed, 100 warnings in 10.00s
Fallo: TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

La integración enumeró micrófonos y grabó 3 s reales (48000 muestras mono
int16 a 16000 Hz); VAD real detectó 0 frames con voz, con RMS 0,0000146337.
Whisper cargó en CPU y Piper informó únicamente CPUExecutionProvider.
La síntesis real de «Hola Pablo, soy Eon, y mi voz ya funciona en local.»
produjo `.runtime/fase3_tts_sample.wav`, 129580 bytes, mono a 22050 Hz,
duración 2,937324263 s, disponible para escuchar manualmente.

La transcripción de ese WAV devolvió `""` con un error controlado y logging:
faster-whisper 1.2.1 usa `metadata_errors` al abrir audio y PyAV 19.0.1
ya no admite ese argumento. No hubo round-trip correcto. La integración se
interrumpió antes de cargar el wake-word real y de realizar su escucha de 2 s;
esas comprobaciones quedan pendientes. No se repitió la ejecución real.
Se necesita corregir/pinear una versión compatible de PyAV y autorización
de Pablo para repetir la integración, limitada en el encargo a una ejecución.

Snapshots reales NVIDIA: GPU 0, RTX 4060 Laptop, antes 0/8188 MiB y después
0/8188 MiB. El basal observado en esta sesión fue 0 MiB, no el aproximado
de 8 MiB del encargo. Ollama quedó sin modelos residentes. La búsqueda
de TODO/placeholder/stub/mock en core/ no encontró coincidencias.
La evidencia local ignorada por Git está en `.runtime/fase3_voice_integration.json`,
`.runtime/fase3_integration.xml` y `.runtime/fase3_mocked.xml`.

### Deuda técnica conocida de voz — 2026-10-07

- Incompatibilidad funcional faster-whisper/PyAV descrita arriba: bloquea
  la validación real. PyAV 18.1.0 es candidato sin instalar ni validar aún.
- Sounddevice produce DeprecationWarning al asignar `shape` en NumPy 2.5;
  revisar su compatibilidad al actualizar estas dependencias.
- Wake-word inglés provisional; no se detecta todavía una frase custom
  «Hey Eon» y la prueba de escucha real de silencio sigue pendiente.
- Barge-in no dispone de cancelación de eco ni identificación de hablante:
  el audio propio por altavoces puede activar una interrupción falsa. No se
  implementa playback ni su cancelación en esta fase.
- Pesos descargados/cache locales ignorados requieren preparación explícita
  en otra instalación; Hugging Face advierte de copias adicionales al no
  disponer de symlinks en este Windows.

Fase 3 NO está completa. No se publica una fase con validación real fallida,
no se borra la rama, no se hace merge a main y no se inicia Fase 4.

## Fase 3 completa: integración real corregida por entrada NumPy — 2026-10-07

Esta entrada posterior actualiza el estado de Fase 3 sin reescribir el registro
de la integración fallida. Pablo autorizó explícitamente el bypass del decoder
PyAV y una única repetición real tras el arreglo, realizada en
`feature/fase-3-voice-engine`. Se leyeron completos STATUS.md y CONTRACTS.md
antes de modificar código. No se cambió ninguna dependencia, ningún pin ni
el código de terceros: PyAV sigue en 19.0.1 y faster-whisper en 1.2.1.

VoiceEngine lee WAV PCM con `wave`, normaliza a float32, promedia canales y
remuestrea a 16000 Hz mediante interpolación lineal NumPy. Whisper recibe
siempre un array, nunca una ruta ni un objeto file-like; no usa `decode_audio`.
Se admite PCM sin comprimir de 8/16/24/32 bits y arrays/buffers, incluida la
pareja `(sample_rate, audio)` de AcousticDetector.record. MP3/OGG, WAV
comprimido/float y objetos file-like quedan fuera del alcance. El remuestreador
básico no aplica filtro antialias y no se presenta como una solución hi-fi.
Los contratos reflejan estos límites y DECISIONS.md, sección 13, registra
la decisión y el motivo de no downgradear PyAV.

Resultados reales de la única repetición autorizada tras el arreglo:

- Micrófono real: 3 s, 48000 muestras mono int16 a 16000 Hz. VAD real detectó
  0 frames con voz; RMS 0,0003670703. Se esperaba silencio y no es un fallo.
- STT real: Whisper `base`, CPU/int8; TTS real: `es_ES-davefx-medium`,
  exclusivamente CPUExecutionProvider. Texto sintetizado: «Hola Pablo, soy Eon,
  y mi voz ya funciona en local.»
- Texto REAL transcrito: «Hola Pablo, soy Ian y mi voz ya funciona en local.»
  Es el resultado sin corregir el nombre ni sustituirlo por el texto original.
  El round-trip pasó: texto no vacío y ningún error de transcripción.
- Wake-word real `hey_jarvis`: disponible, sus tres sesiones ONNX informaron
  únicamente CPUExecutionProvider; `listen_once(2.0)` devolvió `False` en
  silencio, sin error ni crash. Continúa siendo PROVISIONAL hasta «Hey Eon».
- WAV actual `.runtime/fase3_tts_sample.wav`: 125996 bytes, mono PCM int16
  a 22050 Hz, duración 2,856054422 s, generado realmente y no reproducido
  automáticamente. Su tamaño/duración corresponden a esta nueva síntesis.
- NVIDIA GPU 0, RTX 4060 Laptop: antes 0/8188 MiB, después 0/8188 MiB.
  Ollama quedó sin modelos residentes; la voz no cargó modelos en Ollama.

```text
pytest tests/ -v -s, EON_RUN_VOICE_INTEGRATION=1:
190 passed, 1 skipped, 124 warnings in 13.33s
pytest tests/ -q -rs, sin variables de integración:
189 passed, 2 skipped in 2.32s
```

La suite mantiene los 167 casos sin hardware anteriores y añade 22 casos
para la corrección: lectura PCM real, normalización de muestras, estéreo,
frecuencias 8000/16000/22050/32000/48000, arrays/buffers y su frecuencia,
interpolación, rechazo de archivos inválidos/comprimidos/vacíos/truncados y
garantía de no llamar a PyAV. En la suite con hardware solo se omitió la
integración Ollama; en la suite sin hardware se omitieron ambas integraciones.
Las 124 advertencias son DeprecationWarning de sounddevice con NumPy 2.5;
no se suprimieron y siguen siendo deuda técnica, sin fallos de los tests.

Evidencia local ignorada por Git:
`.runtime/fase3_voice_integration_corrected.json`,
`.runtime/fase3_corrected_pytest_output.txt`,
`.runtime/fase3_corrected_integration.log`,
`.runtime/fase3_corrected_integration.xml`,
`.runtime/fase3_corrected_mocked_output.txt` y
`.runtime/fase3_corrected_mocked.xml`. La evidencia JSON/XML de la ejecución
fallida anterior se conserva separada, sin sobrescribirla.

Fase 3 SÍ está completa en su alcance CPU-only, con contratos, tests y
validación real. Persisten las limitaciones documentadas: decoder PyAV
incompatible fuera de la vía implementada, remuestreo básico, wake-word
inglés provisional, advertencias sounddevice/NumPy y barge-in sin cancelación
de eco ni identificación de hablante. No se afirma haber implementado playback,
GUI ni Voice Fingerprint Lock.

Pablo autorizó el commit de cierre con mensaje
`feat: Fase 3 - Voice Engine STT/TTS, VAD, wake-word y barge-in sobre CPU`
y el push únicamente de `feature/fase-3-voice-engine` tras estas verificaciones.
`main`, `core/model_router.py`, `safety/`, GUI y visión siguen sin modificaciones.
No se hace merge a main ni se elimina la rama. Fase 4 no se inicia sin una
nueva confirmación explícita de Pablo tras revisar la integración exitosa.
