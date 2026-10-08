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

## Fase 3: muestras de ocho voces para decisión humana — 2026-10-07

A petición de Pablo, se creó `scripts/fase3_voice_sampler.py` como diagnóstico
de una sola vez, no como módulo de runtime ni decisión de voz. Se ejecutó una
única vez en `feature/fase-3-voice-engine` con Piper ya instalado, sin nuevas
dependencias, sin micrófono, STT, playback ni cambios de configuración.

Se generaron dos WAV reales por cada ID: es_ES-carlfm-x_low,
es_ES-davefx-medium, es_ES-mls_10246-low, es_ES-mls_9972-low,
es_ES-sharvard-medium, es_MX-ald-medium, es_MX-ald-x_low y es_MX-claude-high.
Los sufijos son `_presentacion.wav` y `_nombre.wav`, con los dos textos exactos
solicitados por Pablo, dentro de `.runtime/voice_samples/`. Las ocho voces
confirmaron CPUExecutionProvider exclusivo; se cargaron secuencialmente con
`use_cuda=False`. Sharvard tiene dos hablantes: se usó el predeterminado,
speaker 0 (`M`), sin generar una muestra del speaker 1.

Se reutilizaron los pesos y JSON de davefx desde la caché local verificada;
se descargaron las otras siete voces del catálogo oficial de Piper. Todas
las descargas usan timeout, try/except con logging, comprobación de tamaño
y MD5 del catálogo, y publicación atómica para no dejar descargas parciales
como caché válida. Se registró tamaño y duración de cada WAV. Se verificaron
los 16 WAV PCM mono int16 completos, 3825344 bytes en conjunto.

El informe local ignorado por Git es
`.runtime/voice_samples/sampler_report.json`; el log y tabla completos están
en `.runtime/fase3_voice_sampler.log`. NVIDIA mostró 0/8188 MiB antes y
después de la generación. Las muestras no se reprodujeron automáticamente.

No se añadieron tests en tests/: es una herramienta diagnóstica de apoyo a
la elección humana, con la excepción explícita solicitada por Pablo.
user_settings.json, DECISIONS.md, voice_engine.py y config.py permanecen
sin cambios. No se hizo commit ni push: HEAD sigue en `35f6965`; solo quedan
el script nuevo y este registro de sesión sin publicar. main no se modificó.
La elección de voz permanece pendiente de la escucha y decisión de Pablo;
no se continúa a ningún otro paso.

## Fase 3: afinación exploratoria de Sharvard — 2026-10-07

Pablo solicitó el diagnóstico previo de las rutas de síntesis y un barrido
local de parámetros, sin modificar producción ni decidir todavía la voz.
Se leyeron completos STATUS.md y CONTRACTS.md. VoiceEngine y el sampler
pasan el texto completo en una llamada a Piper; no fragmentan ni concatenan
WAV externos. Piper 1.8.0 sí fonemiza por frase y sintetiza bloques internos
que synthesize_wav escribe consecutivamente en un único WAV, sin añadir
silencio configurable. Normaliza amplitud por bloque. Esto describe el
procesamiento, no demuestra por sí solo la causa de una impresión auditiva.

Se inspeccionaron las firmas y código instalados: SynthesisConfig tiene
speaker_id, length_scale, noise_scale y noise_w_scale con default None
(heredan de la voz), normalize_audio=True y volume=1.0. En Sharvard los
defaults efectivos son speaker 0 (`M`), length_scale=1.0, noise_scale=0.667
y noise_w_scale=0.8. noise_w es la clave del JSON; noise_w_scale es el campo
Python. sentence_silence no existe en estas APIs ni en SynthesisConfig.

Se creó y ejecutó una sola vez scripts/fase3_voice_tuning.py, reutilizando
el escritor WAV atómico del sampler y únicamente Sharvard de la caché local.
No hubo descargas, instalaciones, reproducción, acceso al micrófono, llamadas
a Ollama ni consultas/cargas GPU. Piper confirmó CPUExecutionProvider exclusivo
con use_cuda=False. Se generaron y verificaron diez WAV PCM mono int16:

- A: presentación completa con length_scale 1.0, 1.15 y 1.3, manteniendo
  noise_scale=0.667 y noise_w_scale=0.8. Duraciones reales: 6,501587 s,
  7,256236 s y 7,999274 s, respectivamente.
- B: presentación completa con length_scale=1.15, candidato intermedio
  exploratorio sin selección por escucha; ruido bajo 0.6003/0.72 y alto
  0.7337/0.88 (noise_scale/noise_w_scale), alrededor de los defaults ±10 %.
  Duraciones: 7,221406 s y 7,128526 s. Son muestras estocásticas; no se
  acredita mejora auditiva ni se fija un ganador automáticamente.
- C: entradas exactas «Eón.», «Eo-on.», «E, on.», «Eeeón.» e «Ión.»,
  todas con length_scale=1.0 y ambos ruidos por defecto. Solo son pruebas
  de entrada al sintetizador, no cambios del nombre de EON ni de textos GUI.

Los diez archivos están en .runtime/voice_samples/, junto al informe local
tuning_report.json con parámetros efectivos, textos, tamaños y duraciones.
El log y la tabla completos están en .runtime/fase3_voice_tuning.log.
Todas las muestras usan speaker 0 (`M`), normalize_audio=True y volume=1.0.
La comparación estocástica no garantiza aislar perfectamente cada cambio;
la evaluación de naturalidad/pronunciación queda para la escucha de Pablo.

voice_engine.py, config.py, user_settings.json, DECISIONS.md y main siguen
sin cambios. Se conserva el sampler anterior sin modificar. No se hace
commit ni push: este diagnóstico y el sampler permanecen sin publicar;
HEAD sigue en 35f6965. Solo se añade este registro obligatorio de sesión.
No se continúa a ningún otro paso ni se adopta una decisión de producción.

## Fase 3: cierre con selección humana de voz — 2026-10-07

Tras escuchar manualmente las ocho voces españolas y las variantes de afinación,
Pablo seleccionó es_ES-sharvard-medium, speaker 0 (`M`). Fase 3 pasa de validada
técnicamente a cerrada con selección humana de voz, según la sección 14 de
docs/DECISIONS.md. Se descarta davefx por pronunciación ambigua de «Eon» y
preferencia auditiva de Pablo. La naturalidad sigue siendo aceptable, no definitiva:
cierta artificialidad/trompiconeo y el pulido avanzado de prosodia son deuda de UX
futura, fuera de esta fase.

user_settings.json fija length_scale=1.15, noise_scale=0.7337,
noise_w_scale=0.88 y speaker_id=0. Los alias «Eon»/«EON» → «Eeeón» afectan
solo a la entrada interna de Piper, por palabra completa, sensibles a mayúsculas
y sin sustituciones recursivas. El nombre visible sigue siendo EON/Eon y no
cambian los textos originales, logs de EON ni contenidos de otros módulos.
STT base CPU/int8, VAD 2, 16000 Hz y wake-word hey_jarvis permanecen intactos.
Se extienden la validación estricta, copia profunda, contrato y tests de estos
ajustes. Los ruidos admiten [0, 2] y length_scale [0.5, 2], con números finitos
sin booleanos; el cero de ruido desactiva su componente de variabilidad.

Se conservan scripts/fase3_voice_sampler.py y scripts/fase3_voice_tuning.py como
herramientas diagnósticas de apoyo a la decisión humana, con type hints, logging,
CPU-only y sin código incompleto. Reproducen la comparación exploratoria original;
no sustituyen la configuración de producción ni se vuelven a ejecutar en este
cierre. Los WAV, pesos e informes quedan en .runtime/, ignorados por Git.

Verificación de este cierre:

- Suite completa sin hardware: 245 tests aprobados y 2 omitidos (integraciones
  reales de voz y Ollama), sin fallos. Informe .runtime/fase3_sharvard_mocked.xml.
- Integración real autorizada con Sharvard: 1 test aprobado, 124
  DeprecationWarning de sounddevice/NumPy 2.5; no se ocultan ni se cambian paquetes.
  Captura real de 3 s a 16000 Hz, WAV Piper mono PCM int16 de 22050 Hz,
  159788 bytes y 3.622312925170068 s, usando los ajustes seleccionados.
- Texto real de Whisper: «Hola Pablo, soy Eon y mi voz ya funciona en local.».
  Se conserva literalmente, sin exigir ni forzar la transcripción del nombre.
- Wake-word hey_jarvis escuchó 2 s y devolvió False sin error. Whisper informó
  cpu; Piper y los tres modelos wake-word, CPUExecutionProvider exclusivo.
  NVIDIA mostró 0/8188 MiB antes y después; ollama ps quedó vacío.
- Evidencia local: .runtime/fase3_voice_integration_sharvard.json,
  .runtime/fase3_sharvard_integration.xml y .runtime/fase3_sharvard_integration.log.

Pablo autoriza un commit nuevo y push solo de feature/fase-3-voice-engine.
No se modifica main ni se hace merge. Fase 4 no se inicia.

## Fase 3 fusionada a main — 2026-10-07

Tras la autorización explícita de Pablo y la corrección gramatical de la sección
13 de DECISIONS.md (commit `4fd154978498f80319eb47d508f76efddceca94e`),
la reverificación sin hardware terminó con 245 tests aprobados y 2 omitidos;
Ollama estaba vacío. No se repitieron las integraciones reales de voz ni Ollama.

Fase 3 se fusionó a main mediante --no-ff con el commit de merge
`1815bcedf5fd10f7ba2014fa51e5cf1d8bad631d` y el tag anotado
`fase-3-completa`, ambos publicados y verificados en el remoto. El contenido
del merge coincidió exactamente con el de la rama de fase. Se eliminó
feature/fase-3-voice-engine local y remotamente tras verificar la publicación.
Las entradas anteriores se conservan como registro histórico de cada sesión.
Fase 4 no se inicia; queda pendiente de nueva confirmación explícita de Pablo.

## Fase 4: base del notch implementada, pendiente de validación visual — 2026-10-08

Pablo autorizó esta fase en local, partiendo de main limpio en
ee8b3830b5f912fd0fdbaa3bb7cc5d5bfce4f162, con el merge de Fase 3 y los tres
tags fase-1/2/3-completa comprobados. Se leyeron completos STATUS.md,
CONTRACTS.md y AGENTS.md antes de escribir código. Ollama estaba vacío.
Se creó feature/fase-4-notch-gui; main no se modifica ni se hace merge.

Verificación previa a instalar: PyPI ofrece el wheel
pyqt6-6.11.0-cp310-abi3-win_amd64.whl, compatible con las etiquetas del
Python 3.14.8 de 64 bits local. El dry-run solo binario resolvió las transitivas
sin compilar. Se instaló PyQt6 6.11.0 (pin existente), PyQt6-Qt6 6.11.2 y
PyQt6-sip 13.13.0 mediante --only-binary=:all:, con import real y pip check
correctos. No se cambian requirements.txt, config.py ni user_settings.json.

Implementación de esta base:

- core/eon_state.py: siete estados, colores y labels españoles de solo lectura,
  sin importar Qt. El verde #39ff88 de VISION_ACTIVE es provisional, pendiente
  de confirmación visual de Pablo; los otros hex son los del encargo explícito.
- gui/notch_window.py: NotchGeometryState y NotchController puros, reloj
  inyectable, tick y flags independientes. Auto-hide cuenta N segundos continuos
  en IDLE sin actividad ni ratón encima; al terminar un bloqueo se reinicia el
  intervalo completo. Visión activa fuerza EXPANDED y bloquea cualquier colapso.
- gui/notch_qt.py: adaptador NotchWindow importado perezosamente, QTimer de
  200 ms, ventana frameless/Tool/always-on-top/translúcida, foco deshabilitado
  en PEEK/HOVER_PEEK y permitido en EXPANDED. Posición superior central mediante
  QScreen actual, con eventos de pantalla y ratón protegidos por logging.
- Dimensiones iniciales 220×5, 220×27 y 220×90 píxeles lógicos, ajustables tras
  revisión visual. Son constantes de fase, no ajustes nuevos del JSON.
- gui/char_widget.py: ojo vectorial funcional y animación real de color de
  220 ms. Solo el contenido artístico es temporal; no hay lógica incompleta.
- scripts/fase4_notch_harness.py: notch inicialmente expandido y controles de
  los siete estados, tres flags, expansión/colapso y cierre. Imprime instrucciones
  en español. Esc es colapso local con foco, no hotkey global.

CONTRACTS.md, secciones 12–14, congela las interfaces y garantías; DECISIONS.md,
sección 15, registra wheel, arquitectura, propuestas visuales y límites.
No se conecta todavía el notch a voz/visión reales, no se implementan hotkeys
globales ni arte final del personaje, ni se modifican voz, visión, safety,
Genesis o model_router. Fase 5 no se inicia.

Verificación automática y diagnóstico sin escritorio real:

- pytest completo con QT_QPA_PLATFORM=offscreen y ambas integraciones reales
  desactivadas: 295 aprobados, 2 omitidos en 9.73 s. Se conservan los 245 casos
  previos y se añaden 50 de Fase 4. Informe .runtime/fase4_mocked.xml.
- Cada módulo nuevo en core/gui tiene tests. Los del controlador se ejecutan
  sin QApplication; una prueba separada prohíbe cualquier import PyQt6 y aun así
  importa y ejecuta el controlador. Qt se verifica sin instalar pytest-qt.
- Comprobaciones offscreen de instanciación, flags, dimensiones, visibilidad,
  animación, limpieza del timer y fallo de pantalla controlado. Se ejercitaron
  los siete botones de estado, tres casillas y expansión/colapso del harness.
- Revisión del render offscreen en .runtime/fase4_notch_preview.png: personaje
  y textos visibles al cargar Segoe UI solo para ese diagnóstico, pues este
  backend enumera cero fuentes. No se modificó la tipografía del proyecto.
- Búsqueda TODO/placeholder/stub en core/gui: solo aparece el docstring
  artístico permitido de char_widget.py; ninguna lógica incompleta.
- Ollama continúa vacío. No se cargan modelos ni se repite integración real de
  voz/Ollama; informes y preview de .runtime no se versionan.

Para la prueba humana, desde PowerShell:

```powershell
Set-Location C:\Dev\Eon
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
& .\.venv\Scripts\python.exe .\scripts\fase4_notch_harness.py
```

Pablo debe validar en su pantalla real tamaños, contraste, color de visión,
hover/clic, foco, ausencia de icono del notch en taskbar y auto-hide. Offscreen
no sustituye esa validación ni comprueba composición o varios monitores reales.
El commit y push están autorizados solo para feature/fase-4-notch-gui; la rama
se conserva y el merge queda bloqueado hasta confirmación visual explícita.

## Fase 4: personaje original squircle y cápsula oscura — 2026-10-08

Por encargo de Pablo se rediseñan únicamente el pintado Qt y el personaje en
feature/fase-4-notch-gui, partiendo del commit 0f000bc. Se revisaron los tres
módulos GUI anteriores y se leyeron completos STATUS.md, CONTRACTS.md y AGENTS.md.
Se añade a AGENTS.md la regla permanente de no ingerir/reproducir activos visuales
reservados de terceros. No se consultó, incluyó ni replicó ningún activo visual
de terceros; todo el dibujo procede de la descripción textual original y de
primitivas matemáticas propias en QPainter. No se añade ninguna dependencia.

El diseño adopta el lenguaje genérico de un blob suave en notch, con identidad
propia de EON: cuerpo de superelipse 76:44/exponente 4.5, degradado cálido
#e9d3b8 y color de estado, rostro mínimo, glow y siete insignias vectoriales
distintas. La cápsula permanece #121318, sin colorear toda la barra; solo
personaje/glow/insignia comunican el color activo. La propuesta por estado está
en DECISIONS.md, sección 16, y se mostró en el chat antes de implementarse.
El color verde de VISION_ACTIVE y el acabado siguen pendientes de validación
visual humana; no se afirma una comparación exhaustiva con productos existentes.

Revelado: viewport fijo por geometría, manteniendo 220×5/27/90 y animando durante
220 ms recorte, traslación interior, contorno visible y opacidad del texto.
La dimensión nativa cambia inmediatamente; no se anima el tamaño exterior entero.
PEEK muestra una cresta, HOVER_PEEK una porción intermedia y EXPANDED el cuerpo
completo con texto debajo. No hay ventana invisible de tamaño máximo interceptando
ratón en PEEK. Color, rostro e insignia hacen transiciones suaves y mantienen
su mezcla visible si una transición se interrumpe.

gui/notch_window.py y sus reglas/interfaces permanecen exactamente intactos.
Tampoco cambian la paleta lógica, config.py, user_settings.json ni los 295 tests
existentes; se mantienen foco y posición dinámica QScreen. CharWidget conserva
sus firmas públicas; CONTRACTS.md añade solo la actualización visual posterior.
El harness conserva siete botones, tres casillas y expansión/colapso, con las
instrucciones actualizadas para el nuevo render. No se conecta a voz/visión real.

Verificación: pytest completo con QT_QPA_PLATFORM=offscreen e integraciones reales
desactivadas terminó con 307 aprobados y 2 omitidos en 3.36 s. Incluye los 295
anteriores y 12 nuevos de render: cápsula neutra en cada estado, insignias
distintas, disolución/revelado interrumpidos, cresta visible y controles del
harness operativos. Se comprobó con git diff la ausencia de cambios en el
controlador y tests anteriores. La búsqueda de marcadores de código incompleto
en core/gui/scripts Python no encontró ninguno; Ollama sigue vacío.

Se revisaron renders offscreen de las siete variantes y las tres geometrías,
generados únicamente desde el código de EON. Informe y previews locales:
.runtime/fase4_squircle_mocked.xml, .runtime/fase4_squircle_states.png y
.runtime/fase4_squircle_peek/hover/expanded.png. No se versionan. La fuente de
sistema se cargó solo para el diagnóstico offscreen, no como activo del proyecto.
Estos renders no acreditan fluidez, foco o composición en el escritorio real.

Para probar en pantalla real, desde PowerShell:

```powershell
Set-Location C:\Dev\Eon
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
& .\.venv\Scripts\python.exe .\scripts\fase4_notch_harness.py
```

Pablo debe revisar los siete estados, transiciones, hover/clic y actividad antes
de aprobar. Se autoriza commit y push solo de la rama existente, sin crear otra
ni borrarla. Main permanece intacta, no se hace merge y Fase 5 no se inicia.

## Fase 4: onda radial y panel con controles locales — 2026-10-08

Pablo amplió explícitamente el alcance sobre feature/fase-4-notch-gui, partiendo
de a35b0c9 y sin crear otra rama. Se releen AGENTS.md, STATUS.md, CONTRACTS.md
y los tres módulos GUI antes de escribir código. Main local y remoto permanecen
en ee8b3830b5f912fd0fdbaa3bb7cc5d5bfce4f162, sin merge ni push directo.
La decisión 17 de DECISIONS.md actualiza las rondas anteriores sin reescribirlas;
CONTRACTS.md, sección 16, congela las nuevas interfaces y su alcance.

La cápsula se funde con el borde superior, con esquinas superiores cuadradas,
inferiores redondeadas y posición dinámica QScreen. PEEK pasa a 16 px de alto;
hover conserva 27 px. Ancho cerrado proporcional 280–320 px (300 en 1920).
EXPANDED 460×280 con 18 px de margen, personaje izquierdo, texto de estado,
engranaje original, fila de accesos, mensajes y entrada inferior. Personaje
completamente oculto en PEEK/hover; revelado exactamente 0 en ambos estados.

EonState se comunica ahora principalmente mediante degradado radial suave
(23 % de mezcla, IDLE 3.5 %) y frente de onda de 620 ms. La misma animación
permanece independiente de los tres estados geométricos. Los bloques de texto
tienen superficies oscuras semitransparentes propias. Se anima tamaño NATIVO,
opacidad y revelado en QParallelAnimationGroup: expansión 330 ms OutBack,
colapso 290 ms OutQuad, con continuación desde el tamaño interrumpido. Se
compararon 280/330/350 ms offscreen; eso no acredita fluidez física en Windows.
El personaje incorpora respiración idle 1–1.02, parpadeo aleatorio 3–7 s,
highlight y sombras de volumen/contacto originales, detenidos al ocultarse.

Se consultaron solo metadatos de rutas y dos archivos técnicos externos
autorizados: LICENSE y NotchBuddy/Sources/App/IslandWindowController.swift,
revisión 3992d914625cda3003273348f463a64f67dd152d. La lista y límites están
en DECISIONS.md. No se abrió ningún archivo dedicado al render/animación de
personajes ni assets, sonidos, medios o diseño. No se incorporó ni copió contenido
externo en EON; el diseño y código Qt son propios. No se instala nada.

Implementación funcional adicional:

- config.py valida quick_launch_shortcuts (default []), devuelve copia profunda
  y guarda preferencias mediante validación compartida y publicación atómica.
  user_settings.json solo añade la lista vacía; modelos, voz y demás valores
  seleccionados por Pablo no cambian.
- gui/notch_controls.py tiene su suite propia: iconos QPainter, QDialog real
  para editar auto-hide/accesos, selectores de archivo/carpeta/color, apertura
  real con Popen sin shell y trabajador de medición RMS. Errores con logging
  y mensajes españoles, sin fallos silenciosos ni procesos reales en tests.
- NotchController añade únicamente reload_settings(): conserva las interfaces
  previas, reglas, flags y estado, y reinicia un intervalo completo al aplicar
  preferencias. Diálogo y botones se actualizan sin reiniciar el proceso.
- Campo Qt real, Enter/Enviar con handle_user_message(text: str) -> None:
  vacío no hace nada; no vacío limpia el campo y muestra un acuse honesto.
  No se almacena texto, no se infiere ni se simula una respuesta de IA. El
  borrador bloquea auto-hide; tabulación sigue el orden visual de controles.
- Micrófono opt-in de unos 3 s: get_input_level en QThread, barra de RMS en vivo,
  distinción de silencio/error, una sola captura y bloqueo temporal de auto-hide.
  El cierre solicita cancelación y espera finished de forma no bloqueante antes
  de salir, sin destruir un hilo activo. No se almacena grabación.

Alcance consciente: medidor de nivel de voz real, transcripción y respuesta
pendientes de fase de integración. La ruta de captura está implementada, pero
en ESTA ronda no se ha pulsado el micrófono físico: su validación humana está
pendiente. No se invocan STT/TTS/brain, no se cargan modelos ni se inicia Fase 5.

Verificación final sin hardware: pytest completo, QT_QPA_PLATFORM=offscreen,
EON_RUN_VOICE_INTEGRATION=0 y EON_RUN_OLLAMA_INTEGRATION=0:
366 passed, 2 skipped in 3.32s, sin fallos. Informe local ignorado:
.runtime/fase4_panel_mocked.xml. Los dos omitidos son las integraciones reales
de Ollama y voz. Se mantienen los tests puros de actividad/visión/auto-hide;
los tests Qt anteriores se actualizan únicamente para el nuevo diseño/timing.
59 casos adicionales cubren configuración válida/inválida, publicación fallida,
diálogo real, lanzadores simulados, texto, señales RMS, errores, cancelación,
cierre con hilo activo, tamaños intermedios, onda independiente, vida, márgenes
y foco/tabulación. La búsqueda de marcadores TODO/placeholder/stub en Python
fuera de tests no encuentra ninguno; los nombres externos prohibidos tampoco
aparecen en archivos del proyecto. Ollama muestra solo cabecera, sin modelos.

Renders offscreen propios revisados en .runtime/fase4_panel_shortcuts_preview.png,
.runtime/fase4_panel_peek_preview.png, .runtime/fase4_panel_hover_preview.png
y .runtime/fase4_panel_settings_preview.png;
se corrigieron un fondo claro involuntario y la compresión de cabecera con accesos.
La fuente Segoe UI se cargó solo para el diagnóstico offscreen, no en producción
ni como asset. No se abrieron aplicaciones externas reales ni se guardaron ajustes
personales como parte de las pruebas; los tests usan archivos temporales separados.
También se ejecutó el bucle Qt del harness offscreen y se comprobó que cerrar
su ventana de controles termina el notch y retorna código 0, sin captura real.

Para la revisión real desde PowerShell:

```powershell
Set-Location C:\Dev\Eon
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
& .\.venv\Scripts\python.exe .\scripts\fase4_notch_harness.py
```

Probar los siete estados y la onda mientras se alterna reposo/hover/expandido;
auto-hide y visión conservan sus prioridades. En el engranaje, añadir una ruta
real, elegir color, guardar y comprobar la actualización sin reinicio; un acceso
ausente debe mostrar aviso. Escribir y enviar/Enter verifica acuse y limpieza.
Micrófono activa captura REAL y medidor, sin transcribir. Cerrar durante la
medición debe terminar tras liberar el dispositivo. El guardado modifica realmente
user_settings.json; esos ajustes personales pueden ensuciar el árbol de trabajo.
Offscreen no valida composición, foco de escritorio ni varios monitores físicos.
Solo se autoriza un commit nuevo y push de esta rama, que se conserva; Pablo
debe verlo en pantalla y confirmar antes de cualquier merge o Fase 5.

## Fase 4: pulido visual y corrección del medidor, pendiente de pantalla — 2026-10-08

Pablo validó físicamente 3299020 y autorizó esta ronda sobre la misma rama
feature/fase-4-notch-gui. Se releen los módulos GUI/config/harness, AGENTS.md,
STATUS.md y CONTRACTS.md completos y DECISIONS.md §17 antes de escribir código.
Se conserva el controlador puro sin cambios, tamaños cerrados 280–320×16/27,
reveal 0 cerrado, anclaje superior, paleta, auto-hide, prioridad de visión,
entrada y guardado. No hay consultas de repositorios externos en esta ronda,
instalaciones ni modificaciones de core/voz/safety. Main local y remoto siguen
en ee8b3830b5f912fd0fdbaa3bb7cc5d5bfce4f162; sin merge ni push directo.

Cambios documentados posteriormente en DECISIONS.md §18 y CONTRACTS.md §17:

- Onda horizontal LINEAL centro→lados en PEEK/hover y RADIAL en EXPANDED.
  Forma interpola 180 ms sin reiniciar frente/color de estado (620 ms OutCubic).
  Expandir/colapsar mantienen 330/290 ms aprobados, tamaños nativos reales.
- EXPANDED 680×300, clamp ancho_pantalla−24/altura; mínimo nominal 560 siempre
  subordinado al espacio físico. Margen 18, separaciones 8/12, cabecera 76 y
  personaje 120×76; fila opcional de accesos, mensajes oscuros y entrada 44.
  QSS propio oscuro con hover, foco y labels, sin assets o iconos externos.
- Configuración 520×560 limitada al área disponible: switch, selector
  10/15/20 y personalizado para no sustituir ajustes existentes. Tarjetas con
  nombre editable, ruta abreviada/tooltip, punto de color y eliminación propia.
  Los selectores, guardado validado/atómico y aplicación en vivo se conservan.
- Medición continua opt-in por QThread/get_input_level hasta otro clic o cierre.
  El código anterior ya sondeaba repetidamente, pero solo 3 s, con sqrt(RMS)
  y barra de 6 px; no se acredita una causa física única sin repetir hardware.
  Nuevo módulo puro/test gui/microphone_meter.py: −50→−8 dBFS mapeados a 0–1,
  barra 10 px, pico 300 ms y caída 0.65/s con timer GUI de 40 ms. Silencio bajo
  el suelo visual, señal con dB y error del trabajador se distinguen en español.
  Una sola medición; cancelación entre ventanas, cierre diferido y restauración
  del flag previo. No se guarda audio ni se invoca STT/TTS/brain.
- Halo/cuerpo/highlight/sombra caben con padding interno 6, sin máscara circular
  sobre el blob. Respiración 1–1.02 y parpadeo 3–7 s se conservan; mirada IDLE
  ±2 cada 4–9 s, squash breve y clic/teclado con recoil y bocadillo propio 1.6 s.
  Esa reacción no cambia ningún estado lógico ni flag, ni simula inferencia.

Deuda artística conocida: la identidad profunda del personaje y sensación de
vida dentro del PC siguen pendientes de exploración/aprobación humana. Esta
ronda aporta vida mínima original, no arte definitivo ni integración inteligente.
STT y brain siguen fuera del alcance de Fase 4 y no se inicia Fase 5.

Verificación final sin hardware: pytest completo, QT_QPA_PLATFORM=offscreen,
EON_RUN_VOICE_INTEGRATION=0 y EON_RUN_OLLAMA_INTEGRATION=0:
411 passed, 2 skipped in 3.61s, sin fallos; 45 casos adicionales frente a 366.
Informe local ignorado .runtime/fase4_polish_mocked.xml. Se prueban escala RMS,
pico con reloj, captura continua simulada, silencio/señal/error, cancelación,
cierre seguro, forma por geometría/morph interrumpido, tamaños/clamp, tarjetas,
validación/guardado, vida/clic y halo incluso con movimientos máximos.
El test antiguo de copias de configuración asumía siempre 15 s pese a la
preferencia personal actual de 3 s: ahora compara con el dato realmente
inyectado, sin alterar producción ni las preferencias de Pablo. Los tests de
lista vacía usan una lista aislada, no dependen de sus accesos personales.

Se revisan renders propios de panel, bocadillo, diálogo, PEEK y hover en
.runtime/fase4_polish_*.png. Fuente Segoe UI del sistema cargada solo para
diagnóstico offscreen, sin copiarla. Contrastes calculados: texto principal
#f0f2f6 sobre #292d37 12.29:1; secundario #b9c1d0 7.61:1; error #ffb3b3 sobre
#121318 10.91:1, todos superiores a AA 4.5:1. Bucle Qt del harness comprobado
offscreen: cerrar controles termina notch y retorna 0, sin captura/guardado real.
Búsqueda de marcadores de código incompleto en Python fuera de tests y nombres
externos prohibidos en código/docs: vacía. Ollama muestra solo cabecera.

user_settings.json ya estaba modificado por la prueba humana; se preserva
byte a byte (SHA256 678B5DB676C72B810B0BC796A583469F024083F190789247A002A1840415F015)
y se excluye del commit, por lo que ese cambio previo seguirá visible en status.
docs/BUDGET.md inicia el registro con lecturas 17 %→19 % del límite semanal de
cuenta: aproximadamente 2 puntos, redondeados/compartidos, sin atribución exacta
de tokens ni USD a esta sesión. Solo se autoriza commit nuevo/push de esta rama.

Revisión pendiente de Pablo en su pantalla real, sin variable offscreen:

```powershell
Set-Location C:\Dev\Eon
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
& .\.venv\Scripts\python.exe .\scripts\fase4_notch_harness.py
```

Validar ondas en siete estados y tres geometrías, ancho, tarjetas y guardado,
auto-hide/foco, halo y mirada, clic con bocadillo y estado conservado. Micrófono:
activar, hablar a distintos niveles, observar señal/pico, volver a pulsar para
detener y cerrar durante captura; esta prueba física no se hizo aquí. Medidor
real implementado, transcripción/respuesta pendientes. Offscreen no acredita
composición, periféricos ni fluidez física. La rama se conserva; esperar validación
humana antes de merge o Fase 5.
