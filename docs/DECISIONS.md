# EON — Decisiones

Decisiones adoptadas para el proyecto el 2026-10-07.

## 1. Desarrollo exclusivamente local

El desarrollo se realiza exclusivamente en Codex Desktop, modo «Este ordenador»,
sobre `C:\Dev\Eon`. Se abandona definitivamente cualquier flujo híbrido con
Codex Cloud/App Web para simplificar el proceso. No existe ni existirá un flujo
alternativo de desarrollo en Codex Cloud para este proyecto. El commit y push
al repositorio existente no cambian esta decisión.

## 2. Alcance de la Fase 0

La Fase 0 es exclusivamente scaffolding documental y estructural, con cero
código Python y ningún archivo `.py`. No se implementa ningún subsistema, no se
instalan dependencias ni se ejecuta Ollama. Solo se completa esta fase; las
siguientes necesitan confirmación humana explícita.

## 3. Disco y VRAM son medidas distintas

El tamaño mostrado por `ollama list` representa espacio en disco, no consumo de
VRAM. Ningún documento ni elección de modelo debe asumir equivalencia. La
residencia y el consumo real se medirán en fases posteriores con `nvidia-smi`
y `ollama ps`.

## 4. Embeddings sin exención automática

`nomic-embed-text` NO tiene exención automática de la política de un único modelo
residente en VRAM. Su tratamiento se decidirá después de medir su residencia real.
Cualquier excepción a VRAM Monogamy exige medición y confirmación humana
explícita; mientras no se apruebe, rige la prohibición de residencia simultánea.

## 5. Elecciones de modelo provisionales

Ninguna decisión de modelo (brain, vision, coding, embeddings o auditor de
razonamiento) queda fijada definitivamente antes de medir consumo real en fases
posteriores. Las asignaciones del JSON son defaults pendientes de validar.
La elección de brain/dispatcher se fijará tras la validación A/B de Fase 2.
`phi4` es experimental y nunca será el modelo por defecto.

## 6. Creación diferida de config.py

`config.py` no se crea en Fase 0. Se creará en la raíz cuando exista el primer
módulo real que lo requiera, previsto en Fase 1 con `core/model_router.py`.

## 7. Elecciones auxiliares de esta sesión

- Se incluyen las 14 dependencias mínimas solicitadas, con versiones exactas
  publicadas no retiradas consultadas en [PyPI](https://pypi.org/) el 2026-10-07.
  Son una propuesta inicial, no un conjunto instalado o validado. Se revisarán
  en la fase de instalación real; no se eligen librerías de TTS Piper ni de
  Voice Fingerprint Lock todavía.
- Los documentos y `.bat` se escriben en UTF-8. Los `.bat` usan finales de línea
  CRLF y página de códigos 65001 para ejecutarse y mostrar mensajes en español
  correctamente en Windows.
- `install.bat` prueba primero el lanzador `py -3` y después `python`, sin ejecutar
  scripts Python. Consulta únicamente `/api/version` del servicio Ollama local,
  con timeout de 3 segundos; no inicia el servicio ni realiza inferencia.
- `install.bat` devuelve código 1 si falta alguna comprobación y 0 si todas pasan.
  `start.bat` devuelve 0 después del aviso. Estos códigos facilitan comprobar los
  esqueletos sin convertirlos en instaladores o lanzadores de EON.
- Se propone una paleta inicial por actividad lógica en `SPEC.md` para concretar
  el diseño pedido. Es provisional; tonos, contraste y estados concretos se
  validarán en la fase de GUI, con indicaciones textuales además del color.
- Git no tenía identidad de autor configurada. Se configura solo en este
  repositorio el usuario de GitHub `PabloDLF06`, con la dirección privada
  `124751066+PabloDLF06@users.noreply.github.com`. Se evita publicar su correo
  personal y no se modifica la configuración global de Git.

## 8. Corrección posterior solicitada por Pablo — 2026-10-07

Pablo solicitó corregir el nombre de autor y committer de los dos commits de
Fase 0 a `PabloDLF06`, manteniendo el correo
`124751066+PabloDLF06@users.noreply.github.com`, sus mensajes, contenido y fechas.
La sección 7 conserva la decisión de usar una identidad local y un correo
privado, sustituyendo únicamente la referencia al nombre legal completo por el
usuario de GitHub `PabloDLF06`.

Esta corrección posterior busca minimizar la exposición de datos personales
en el historial público del repositorio. Se registra en un commit documental
adicional, separado de los dos commits reescritos, para preservar su contenido
original y dejar constancia explícita de la solicitud y del motivo del cambio.

## 9. Implementación de Fase 1 — 2026-10-07

Decisiones técnicas de esta sesión, dentro del alcance del router local:

- Se crea `.venv` con el Python local 3.14.8, instalando solo `requests==2.34.2`
  y `pytest==9.1.1` y sus dependencias transitivas. El entorno y los informes de
  pruebas en `.runtime/` son locales e ignorados por Git; no se instala el resto
  del stack previsto ni se modifica el inventario de dependencias propuesto.
- La configuración se lee en la importación de `config.py`, desde la ruta del
  módulo, como JSON UTF-8 (con soporte de BOM). Los accesores devuelven copias
  para impedir que un consumidor altere accidentalmente la configuración cargada.
  No se inventan defaults, se validan los cinco roles y los demás valores expuestos.
  No se implementa recarga dinámica de configuración en esta fase.
- `keep_alive` vale `5m` por defecto y se configura en `OllamaProvider`, con
  duraciones positivas. La descarga utiliza 0. El timeout HTTP es una pareja
  de 3 segundos de conexión y 120 segundos de lectura, ambos configurables.
  No hay reintentos automáticos de inferencia que puedan duplicar una petición.
- La comprobación de residencia sondea `/api/ps` hasta 5 segundos, cada 0,1
  segundos, con ambos tiempos configurables. Un error de consulta es un estado
  desconocido que bloquea la carga; nunca se interpreta como una lista vacía.
- Un `RLock` compartido serializa toda la operación del router y el ciclo de
  vida del proveedor dentro del proceso. Se verifica la residencia antes y
  después de generar, se normaliza una etiqueta omitida a `:latest`, y se cuentan
  todos los residentes, también los que Ollama sitúe en CPU.
- Se bloquea la carga si existe un modelo externo distinto o residencia múltiple.
  No se descargan modelos de otros clientes automáticamente. Si el único residente
  coincide con el solicitado, se reutiliza y pasa a gestionarse como modelo activo
  del router. El bloqueo de EON no controla procesos externos al suyo.
- Los fallos operativos se registran: carga/descarga devuelven `False` si no se
  verifican; las consultas y la generación lanzan `ProviderError`, que `route`
  captura devolviendo `""`. Esta cadena significa solicitud fallida, no respuesta
  inventada. Configuración inválida y providers no implementados conservan errores
  explícitos. Tras una carga no confirmada se intenta descargar el modelo solicitado.
- La API real de Ollama rechaza la generación para `nomic-embed-text`, incluso en
  una carga con prompt vacío. Solo ante ese HTTP 400 concreto se usa `/api/embed`
  con `input: []`, que carga el modelo sin producir vectores. La descarga sigue
  usando `/api/generate` y `keep_alive: 0`. Es soporte del ciclo de vida exigido,
  no una implementación de embeddings ni de memoria. Referencias oficiales:
  [API de generación](https://docs.ollama.com/api/generate),
  [API de embeddings](https://docs.ollama.com/api/embed).
- La generación devuelve texto completo sin streaming. Admite `options`, `system`,
  `format`, `raw`, `think` y `suffix`; las claves de identidad, prompt, streaming
  y keep-alive se protegen contra sobreescritura. No se incorporan imágenes.
  El HTTP se restringe a loopback, sin redirecciones ni proxies/credenciales del
  entorno, para mantener explícito el alcance local de esta fase.
- Los eventos usan el logger `core.model_router` y el mensaje
  `timestamp=<UTC ISO 8601> evento=<evento> rol=<rol> modelo=<modelo> resultado=<resultado> vram=<snapshot>`.
  Los mensajes de diagnóstico están en español, sin prompts, y no se configuran
  handlers globales al importar el módulo. La aplicación futura elegirá los destinos.
- `nvidia-smi` se ejecuta sin shell, con timeout de 5 segundos. Su CSV se convierte
  a una lista por GPU con índice de salida, uso y capacidad en MiB. Si falla, se
  registra una advertencia y se devuelve una lista vacía solo para la telemetría;
  no se interrumpe el router. Estas lecturas no eligen modelos ni autorizan
  excepciones de VRAM Monogamy.
- La integración real exige `EON_RUN_OLLAMA_INTEGRATION=1`, se omite por defecto
  en CI y requiere un inventario de residentes vacío al comenzar. Descarga el
  modelo en `finally`, incluso ante un fallo de verificación. Se ejecutó una vez
  en esta sesión, dentro de la suite completa.

Las asignaciones de modelos de `user_settings.json`, incluido `qwen3:8b` como brain,
siguen pendientes de la validación A/B de Fase 2. No se fija ninguna decisión
definitiva de modelo con la prueba de residencia de esta fase.

## 10. Cierre de auditoría de Fase 1: Ollama exclusivamente local — 2026-10-07

Durante la implementación de Fase 1 se decidió que `OllamaProvider` restringiera
`base_url` por diseño a HTTP con host `localhost`, `127.0.0.1` o `::1`, rechazando
cualquier host remoto. Es una decisión de seguridad alineada con la filosofía
local-first de EON, no una limitación accidental ni un cambio funcional de esta
auditoría.

Si en el futuro se necesita apuntar a un Ollama remoto, por ejemplo en otra
máquina de la red local, ampliar los hosts admitidos requerirá una decisión
explícita y documentada en `docs/DECISIONS.md`. No se permitirá introducir esa
ampliación mediante un cambio silencioso.

En esta auditoría, `pip freeze` de `.venv` confirmó `requests==2.34.2` y
`pytest==9.1.1`, exactamente las versiones pineadas en `requirements.txt` de
Fase 0. No hubo desviación y no se modificó `requirements.txt`.

## 11. Fase 2: qwen3:8b fijado como brain definitivo — 2026-10-07

Tras revisar el benchmark objetivo de Fase 2 en
[`fase2_brain_report.md`](fase2_brain_report.md), Pablo confirmó explícitamente
que `qwen3:8b` queda fijado como modelo definitivo del rol `brain`/dispatcher
de EON. Esta decisión posterior actualiza, únicamente para `brain`, la
provisionalidad registrada en la sección 5, que se conserva sin reescribir.
Las elecciones de los demás roles siguen pendientes de sus validaciones.

La decisión se basa en el 96,67 % de precisión total (29/30) y el 100 % en
`needs_clarification` (5/5) de `qwen3:8b`, sin fallos de formato. Se descarta
`llama3.1:latest` para este rol por su menor precisión total, 83,33 % (25/30),
y especialmente por su 60 % en `needs_clarification` (3/5). Se descarta
`gemma2:9b` por su 26,67 % de precisión total (8/30), su 0 % en
`needs_clarification` (0/5) y su incumplimiento de formato poco fiable:
19/30 respuestas (63,33 %) no respetaron la salida JSON sin Markdown.
Estos resultados corresponden a los 30 casos y condiciones del benchmark;
no garantizan autonomía ni generalización fuera de esa muestra.

La latencia media observada en estado estacionario de `qwen3:8b` fue
6,608 segundos por clasificación (aproximadamente 6,6 s), calculada sobre
las 29 llamadas posteriores a la primera, que incluía la carga. Es un
trade-off consciente y aceptado explícitamente por Pablo a cambio de la
precisión y la detección de peticiones que requieren aclaración humana.
El diseño previsto del notch ya contempla visualmente esta espera mediante
el estado `THINKING` (procesamiento, violeta). Es una previsión de interfaz,
no una GUI ni un estado de runtime ya implementados; su validación visual
corresponde a la fase de GUI.

El benchmark usó el modo de razonamiento predeterminado de `qwen3:8b`:
no se desactivó `think`. Ajustarlo queda abierto como opción futura en una
fase posterior si la latencia resulta un problema real de UX, siempre con
una nueva evaluación explícita. No se investiga ni se cambia ese modo ahora.

Se verificó que `model_assignments.brain` en `user_settings.json` ya contenía
`{"provider": "ollama", "model": "qwen3:8b"}`, coincidiendo con el default de
Fase 0. No se modificó el archivo; cambia el carácter de la elección, de
provisional a definitivo, mediante esta confirmación humana documentada.

## 12. Fase 3: voz CPU-only y validación pendiente — 2026-10-07

Por autorización explícita de Pablo, se implementan AcousticDetector,
WakeWordDetector, VoiceEngine y BargeInDetector en la rama local
`feature/fase-3-voice-engine`, sin modificar `main`, `core/model_router.py`,
`safety/`, GUI, visión ni componentes de fases posteriores. Los contratos
añadidos describen las interfaces implementadas, no una integración real
completamente validada: la prueba real falló y esta fase no se da por cerrada.

### Dependencias y compatibilidad observada

Se usa el `.venv` existente con Python 3.14.8. Versiones instaladas:
`sounddevice==0.5.6`, `faster-whisper==1.2.1`, `openwakeword==0.6.0`,
`webrtcvad-wheels==2.0.14.post1`, `piper-tts==1.8.0` y `numpy==2.5.3`.
Entre las dependencias transitivas relevantes se instalaron
`ctranslate2==4.8.2`, `onnxruntime==1.30.0` y `av==19.0.1`.
`requests==2.34.2` y `pytest==9.1.1` conservan los pins de Fase 0.
No se instaló PyQt6, mss, pyautogui, onnxruntime-gpu ni dependencias CUDA/cuDNN.

Los pins de sounddevice, faster-whisper y openwakeword se conservan.
El `webrtcvad==2.0.10` propuesto en Fase 0 compiló en este equipo, pero su
importación falló porque depende de `pkg_resources`, ausente en el entorno.
Se sustituyó por `webrtcvad-wheels==2.0.14.post1`, que proporciona el módulo
`webrtcvad` real e importa correctamente sin introducir setuptools por ese
motivo. Se añade el pin de NumPy porque se importa directamente en el código.
Se elige y añade `piper-tts==1.8.0`, disponible como wheel para Windows y
compatible por importación y síntesis real en este Python; su API permite
`PiperVoice.load(..., use_cuda=False)` y `synthesize_wav`.
Referencias: [Piper en PyPI](https://pypi.org/project/piper-tts/1.8.0/),
[WebRTC VAD wheels](https://pypi.org/project/webrtcvad-wheels/2.0.14.post1/) y
[API Python de Piper](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).

Todas las librerías de voz importaron realmente y `pip check` no detectó
conflictos declarados. Sounddevice encontró su PortAudio de Windows
V19.7.0-devel; no hubo que instalar una DLL manualmente. Esto no garantiza
compatibilidad funcional conjunta: la integración detectó después un fallo
entre faster-whisper 1.2.1 y PyAV 19.0.1 al decodificar la ruta de un WAV.
Faster-whisper llama a `av.open(..., metadata_errors="ignore")`; PyAV 19
eliminó ese argumento. La operación devuelve `""`, registra el error y no
derriba el proceso. Referencia:
[changelog oficial de PyAV](https://pyav.basswood.io/docs/stable/development/changelog.html).
PyAV 18.1.0, con wheel de Windows compatible con Python 3.14, es un candidato
de corrección todavía no instalado ni validado en esta sesión. No se cambia
silenciosamente el entorno tras la única ejecución real autorizada.

### CPU-only, modelos y configuración

STT, TTS y wake-word corren exclusivamente en CPU por decisión explícita de
esta fase, para preservar VRAM Monogamy sin acoplar aún VoiceEngine al
ModelRouter. No es un olvido del router ni autoriza inferencias GPU externas.
Whisper se carga con `device="cpu"`, Piper con `use_cuda=False` y las tres
sesiones ONNX de wake-word con `CPUExecutionProvider`; se rechazan providers
inesperados. Las cargas son lazy y los modelos/cache se guardan únicamente
en `.runtime/voice_models/`, ignorado por Git. El runtime no descarga pesos
automáticamente: esta sesión realizó su preparación explícita.

Se selecciona STT `base`, `cpu`, `int8`, para comenzar con un modelo multilingüe
compacto y cuantizado, sin atribuirle todavía precisión o latencia medidas
en el round-trip, que no llegó a ejecutar inferencia por el fallo de PyAV.
La configuración valida estrictamente las siete claves de `voice_settings`:
modelo permitido, CPU, int8, Piper, VAD entero de 0 a 3, 16000 Hz y un ID
built-in real de wake-word. El getter devuelve una copia independiente.

El catálogo real de Piper consultado contiene estas voces españolas:
`es_ES-carlfm-x_low`, `es_ES-davefx-medium`, `es_ES-mls_10246-low`,
`es_ES-mls_9972-low`, `es_ES-sharvard-medium`, `es_MX-ald-medium`,
`es_MX-ald-x_low` y `es_MX-claude-high`. Se elige
`es_ES-davefx-medium`, es_ES medium de un único hablante, como equilibrio
inicial de calidad/tamaño sin selección adicional de speaker. Se sustituye
`voice_profile: pending_selection` por ese ID real. Sus pesos ONNX descargados
ocupan 63.201.294 bytes; la síntesis real generó un WAV válido mono de 22050 Hz.
Catálogo: [voces Piper](https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json).

La versión instalada de openwakeword enumera realmente `alexa`, `hey_mycroft`,
`hey_jarvis`, `hey_rhasspy`, `timer` y `weather`. Se elige `hey_jarvis` de forma
PROVISIONAL, no como equivalente de «Hey Eon»: detecta una expresión inglesa.
Se prepararon solo ese clasificador ONNX y sus dos extractores de features.
El modelo custom «Hey Eon» requiere entrenamiento y validación en una fase
posterior. Referencia: [openWakeWord](https://github.com/dscripka/openWakeWord).
La escucha real de dos segundos quedó sin ejecutar debido al fallo anterior
del round-trip; los tests unitarios sí cubren detección y silencio simulados.

### Resultado real, límites y deuda

La única integración real autorizada se ejecutó una vez: grabó 3 s reales
(48000 muestras mono int16 a 16000 Hz), obtuvo 0 frames con voz según VAD
y RMS 0,0000146337. Cargó Whisper real en CPU y Piper real con
`CPUExecutionProvider`. Piper sintetizó «Hola Pablo, soy Eon, y mi voz ya
funciona en local.» en `.runtime/fase3_tts_sample.wav`: 129580 bytes,
2,937324263 s. El resultado de transcripción fue `""` por la incompatibilidad
descrita, no una transcripción válida ni una prueba de silencio.

NVIDIA GPU 0 mostró 0/8188 MiB antes y después, y Ollama permaneció sin modelos
residentes. Son snapshots, no mediciones continuas de picos; la evidencia
adicional del dispositivo CPU de Whisper y del provider CPU de Piper confirma
la configuración real de los modelos que llegaron a cargarse. No se afirma
haber cargado el wake-word real en esta integración.

La suite sin hardware pasó 167 tests, omitiendo las dos integraciones reales.
La integración de voz falló con 1 test fallido y 100 DeprecationWarning de
sounddevice por asignar `shape` sobre arrays NumPy 2.5; no se ocultaron esas
advertencias. Falta corregir/pinear PyAV y validar de nuevo el round-trip y
la escucha wake-word con autorización explícita para repetir la prueba real.

Además: BargeInDetector usa VAD y energía, sin cancelación de eco acústico ni
identificación de hablante; el propio TTS reproducido por altavoces puede
producir una falsa interrupción. No se implementa playback, su cancelación,
GUI ni Voice Fingerprint Lock. La exclusión del micrófono solo coordina los
detectores de este proceso, no aplicaciones externas. El cache Hugging Face
funciona sin symlinks en este Windows, con posibles copias adicionales en
disco; no se cambió el modo de desarrollador ni se elevó el proceso.

No se hace commit ni push de una fase que no ha superado su validación real.
Se conservan los cambios locales y la evidencia para corregir el fallo sin
repetir automáticamente la integración ni avanzar a Fase 4.

## 13. Fase 3: bypass de PyAV con entrada NumPy y alcance WAV PCM — 2026-10-07

Pablo decidió explícitamente corregir el fallo original
`TypeError: open() got an unexpected keyword argument 'metadata_errors'`
sin downgradear PyAV ni modificar las dependencias existentes. Faster-whisper
1.2.1 llama a `decode_audio` cuando recibe una ruta o un objeto file-like,
y ese decoder invoca un argumento eliminado en PyAV 19.0.1. Si recibe un
ndarray NumPy, omite ese decoder; se adopta esta vía de entrada directa.

Se descarta pinear PyAV 18.1.0 en esta corrección por decisión de Pablo de
mantener el entorno y evitar el riesgo de no disponer de una wheel compatible
con Python 3.14.8/Windows, que obligaría a compilar con FFmpeg nativo. Ese
riesgo es un criterio preventivo, no un fallo observado: en la sesión anterior
se encontró una wheel Windows/Python 3.14 publicada para 18.1.0, pero no se
instaló ni se validó en este entorno. No se afirma que dicha wheel no exista.
PyAV instalado sigue en 19.0.1 y faster-whisper en 1.2.1; no se cambian pins,
no se instalan paquetes ni se parchea código de terceros en `.venv`.

VoiceEngine lee los WAV con el módulo estándar `wave`, admite PCM sin
comprimir de 8/16/24/32 bits y normaliza a float32 entre -1 y 1 (int16 dividido
entre 32768.0). Promedia los canales a mono y remuestrea a 16000 Hz mediante
interpolación lineal NumPy en una utilidad privada con type hints y docstring.
Este remuestreador básico se adopta para STT, no para calidad hi-fi: no tiene
filtro antialias y mantiene la última muestra en el límite final.

También se aceptan arrays, buffers PCM int16 y la pareja `(sample_rate, audio)`
devuelta por AcousticDetector.record; sin frecuencia explícita se asumen
16000 Hz. Whisper siempre recibe un ndarray mono float32 a 16000 Hz, nunca
una ruta ni un objeto file-like, por lo que la transcripción no pasa por PyAV.

Solo-WAV PCM es un alcance consciente de Fase 3 para entradas de archivo.
No se admiten MP3/OGG ni formatos comprimidos (tampoco WAV float). Su soporte
futuro exige resolver la incompatibilidad PyAV/faster-whisper de forma
explícita, validando una versión compatible y una wheel real para el Python
en uso, como decisión separada y documentada. No se incorpora ese soporte
implícitamente en esta corrección ni se añaden scipy/librosa.

Pablo autorizó una única repetición de la integración real tras el arreglo,
incluida la escucha wake-word pendiente. Se conserva la sección 12 y la
evidencia de la ejecución fallida, sin reescribirlas. El commit/push de la
rama queda condicionado a superar esta integración y la suite sin hardware.

La repetición autorizada pasó: Whisper CPU transcribió realmente
«Hola Pablo, soy Ian y mi voz ya funciona en local.» a partir del WAV de Piper
de 22050 Hz convertido a un array mono float32 de 16000 Hz. No se corrige
«Ian» a «Eon» en la evidencia. La escucha real de `hey_jarvis` durante 2 s
devolvió `False` sin error; los tres modelos ONNX informaron exclusivamente
CPUExecutionProvider. La suite con integración terminó con 190 tests
aprobados, 1 omitido (Ollama) y 124 DeprecationWarning de sounddevice/NumPy.
NVIDIA mostró 0/8188 MiB antes y después y Ollama quedó vacío. La deuda del
decoder PyAV sigue vigente para formatos fuera del alcance WAV PCM; el
bypass resuelve la entrada implementada, no repara la librería de terceros.

## 14. Fase 3: selección humana de Sharvard y ajuste TTS — 2026-10-07

Pablo escuchó manualmente las muestras de las ocho voces españolas reales de
Piper, generadas con scripts/fase3_voice_sampler.py, y las variantes de Sharvard
de scripts/fase3_voice_tuning.py. Se descarta es_ES-davefx-medium como voz final
por la pronunciación ambigua del nombre «Eon» y la preferencia auditiva de Pablo.
Se fija es_ES-sharvard-medium, speaker 0 (`M`), como voz seleccionada de Fase 3.

Se adoptan los parámetros elegidos al escuchar las muestras:
length_scale=1.15, noise_scale=0.7337 y noise_w_scale=0.88, con speaker_id=0.
Corresponden a la velocidad de sharvard_length_1.15_presentacion.wav y al ajuste
de naturalidad de sharvard_noise_alto_presentacion.wav. Se mantiene Piper 1.8.0
con use_cuda=False y CPUExecutionProvider exclusivo; STT, VAD y wake-word no
cambian. normalize_audio=True y volume=1.0 mantienen los defaults reales.

La variante preferida sharvard_nombre_eeeon.wav motiva el alias interno
«Eon»/«EON» → «Eeeón», aplicado solo a la entrada del sintetizador. Las
coincidencias son por palabra completa, sensibles a mayúsculas y no recursivas.
El nombre visible del proyecto sigue siendo EON/Eon; no se cambian textos de
interfaz, logs de EON, documentación del nombre ni textos que reciben otros módulos.

Pablo percibe todavía cierta artificialidad/trompiconeo, pero acepta esta voz
como suficiente para cerrar la fase funcional. No se afirma calidad natural
definitiva: el ajuste fino avanzado de prosodia/voz queda como deuda futura de
UX/pulido, fuera de Fase 3. Se conservan ambos scripts como herramientas de apoyo
a la decisión humana, con logging, type hints y CPU-only; los WAV, modelos e
informes locales de .runtime/ no se versionan. Esta entrada actualiza la elección
provisional de la sección 12 sin reescribir su evidencia histórica.

## 15. Fase 4: notch desacoplado y personaje vectorial — 2026-10-08

Antes de instalar se consultaron los metadatos oficiales de
[PyQt6 6.11.0 en PyPI](https://pypi.org/project/PyQt6/6.11.0/#files).
Existe pyqt6-6.11.0-cp310-abi3-win_amd64.whl, compatible según las etiquetas
del Python local 3.14.8/Windows AMD64 de 64 bits; requiere Python >=3.10.
SHA-256 del wheel publicado:
bd11b459c54dca068e988a42cf838303334f0d441b9d16d92ae6719fcb5ac6ba.
La comprobación pip --dry-run --only-binary=:all: resolvió también wheels de
PyQt6-Qt6 6.11.2 y PyQt6-sip 13.13.0 antes de instalar nada. Después se instaló
solo PyQt6==6.11.0 y esas dos transitivas mediante --only-binary=:all:.
El import real funcionó, Qt runtime informó 6.11.2 y pip check pasó.
No se compila Qt/SIP ni se cambia la librería o el pin de Fase 0.

Se proponen #39ff88 (verde) para VISION_ACTIVE y tamaños de panel 220×5
(PEEK), 220×27 (HOVER_PEEK, 30 % del expandido) y 220×90 (EXPANDED), en
píxeles lógicos. Tanto el verde como las dimensiones están PENDIENTES de
confirmación/ajuste visual de Pablo, no son definitivos. Los otros seis hex
son los indicados expresamente en este encargo; SPEC.md describe colores
generales provisionales, pero no contiene esos hex ni un mapeo exacto de los
siete estados. Prevalece esta especificación explícita posterior, sin reescribir
SPEC.md. Colores acompañados de labels en español evitan depender solo del color.

EonState y su paleta están en core/eon_state.py sin Qt. NotchController vive
en gui/notch_window.py con reloj inyectable y tick puro; NotchWindow se importa
perezosamente desde gui/notch_qt.py. Esa separación adicional de archivo garantiza
que importar el controlador no importa PyQt6, incluso si no está instalado.
Se prueban ambos módulos nuevos y se congela su interfaz en CONTRACTS.md.
No se modifica config.py ni user_settings.json: dimensiones y tiempos de animación
son constantes documentadas de esta fase, y auto-hide consume los ajustes existentes.

Decisiones de interacción complementarias: actividad o ratón encima del panel
reinicia el intervalo idle; al liberar todos los bloqueos se esperan N segundos
completos, sin contar tiempo bloqueado. El ratón encima impide que el panel se
cierre bajo el cursor. Los flags y EonState son independientes; voz/borrador no
fuerzan expansión, visión sí y bloquea también el colapso manual. Se añaden
expand/collapse para el harness sin inventar eventos de ratón; Esc es únicamente
colapso local cuando el panel expandido tiene foco, no una hotkey global.
QScreen usa la geometría real de la pantalla principal, no coordenadas fijas.

CharWidget es un ojo vectorial simple, contenido artístico temporal permitido
porque assets/char/ no contiene sprites. No hay lógica incompleta: cambia de
estado y anima el color con QPropertyAnimation, 220 ms e InOutCubic, conservando
el color actual si se interrumpe la transición. El acabado artístico definitivo
y expresiones con sprites quedan fuera de Fase 4. La revisión UI/UX se centra
en contraste, texto de estado, foco limitado al panel expandido y transición
suave; no reemplaza la paleta y requisitos del encargo por un diseño web.

No se añade pytest-qt: el controlador se prueba sin QApplication y los mínimos
Qt usan directamente una QApplication offscreen y avance determinista de la
animación. El harness manual está separado de la suite. El backend offscreen
en este Windows enumera cero familias de fuentes; para revisar el render se
cargó Segoe UI solo en un diagnóstico local, sin cambiar fuentes del proyecto.
Offscreen no acredita foco real, ausencia de icono en taskbar, composición del
escritorio ni comportamiento con pantallas físicas; Pablo debe probar el harness
antes de autorizar merge. No hay wiring real de voz/visión, hotkey global ni arte
final; tampoco se modifica safety, Genesis o model_router.
