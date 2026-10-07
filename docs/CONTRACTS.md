# EON — Contratos congelados de Fases 1 y 3

Fecha: 2026-10-07. Implementación: `config.py`, `core/model_router.py`,
`core/acoustic_detector.py`, `core/voice_engine.py` y `core/barge_in.py`.
Las clases, firmas y garantías descritas aquí existen en el código de esta fase.

## 1. ModelProvider

Clase base abstracta que hereda de `abc.ABC`. Sus cinco métodos son abstractos,
incluyen type hints y tienen docstrings en inglés. No se puede instanciar la
interfaz; `OllamaProvider` es la implementación concreta disponible.

```python
load_model(self, model_name: str) -> bool
unload_model(self, model_name: str) -> bool
is_model_loaded(self, model_name: str) -> bool
get_currently_loaded_models(self) -> list[str]
generate(self, prompt: str, model_name: str, **kwargs: Any) -> str
```

| Método | Garantía |
| --- | --- |
| `load_model` | Devuelve `True` solo tras verificar la residencia; `False` ante un fallo operativo. Reutiliza el modelo si ya está residente y no lo recarga innecesariamente. |
| `unload_model` | Devuelve `True` solo tras verificar la ausencia del modelo; `False` si no puede confirmar la descarga. Es idempotente cuando el modelo ya está ausente. |
| `is_model_loaded` | Devuelve pertenencia al inventario real de residentes. Lanza `ProviderError` si no puede conocer ese inventario. |
| `get_currently_loaded_models` | Devuelve los nombres reales de todos los residentes. Lanza `ProviderError` si la consulta falla o su respuesta es inválida; nunca sustituye un fallo por `[]`. |
| `generate` | Devuelve la respuesta textual completa. Lanza `ProviderError` ante fallos operativos y `ValueError` ante argumentos inválidos. No simula resultados. |

`ProviderError` es un error recuperable derivado de `RuntimeError`; el router
lo captura. Los errores de configuración y uso no se convierten en respuestas
exitosas ni se ocultan.

Cualquier proveedor de nube futuro de Fase 9 deberá implementar `ModelProvider`
exactamente con esta interfaz. Una modificación de estas firmas o garantías
requiere primero una decisión documentada en `docs/DECISIONS.md` y la actualización
coordinada del contrato y sus tests. No hay proveedores de nube implementados.

## 2. OllamaProvider

La implementación actual utiliza `requests.Session` contra HTTP local. Por defecto:

```python
OllamaProvider(
    base_url: str = "http://localhost:11434",
    keep_alive: str | int = "5m",
    timeout: tuple[float, float] = (3.0, 120.0),
    residency_timeout: float = 5.0,
    poll_interval: float = 0.1,
    session: requests.Session | None = None,
)
```

- La residencia se obtiene con `GET /api/ps`. Una etiqueta omitida equivale a
  `:latest`; las etiquetas explícitas se conservan. Se cuentan también los
  residentes en CPU: no existe exención automática para embeddings.
- Una carga nueva se bloquea si hay cualquier residente distinto, o más de una
  entrada residente. La carga mínima usa `/api/generate` con prompt vacío,
  `stream: false` y `keep_alive` explícito. Si Ollama devuelve específicamente
  HTTP 400 porque el modelo no admite generación, se usa `/api/embed` con
  `input: []` para cargarlo sin producir vectores.
- La descarga de un modelo residente usa `/api/generate`, prompt vacío y
  `keep_alive: 0`; se verifica su ausencia mediante `/api/ps`.
- La verificación de carga/descarga consulta `/api/ps` hasta confirmar el estado,
  con un máximo de 5 segundos y un intervalo de 0,1 segundos por defecto.
- `generate` exige que el modelo solicitado sea el único residente antes de la
  petición. No carga otro modelo implícitamente. Usa `/api/generate`, sin streaming,
  y devuelve `response` solo si es texto y `done` es `true`.
- Los kwargs admitidos son `options`, `system`, `format`, `raw`, `think` y `suffix`.
  `options` debe ser un diccionario. No se permite sobreescribir modelo, prompt,
  streaming o keep-alive, ni introducir imágenes en esta fase.
- `keep_alive` se configura en el constructor como duración positiva sencilla
  (por ejemplo `5m`) o segundos enteros positivos. La descarga interna usa 0.
- Cada llamada HTTP tiene timeout, no sigue redirecciones y no hereda proxies ni
  credenciales del entorno. Timeouts, errores de conexión, respuestas no-200 y
  JSON inválido se capturan y registran con `logging`.

El constructor de Ollama no forma parte de la interfaz común de futuros
proveedores. No se implementa generación de embeddings, visión, descargas de
modelos, gestión de secretos ni proveedores de pago.

## 3. ModelRouter

```python
ModelRouter(provider: ModelProvider | None = None)
route(self, role: str, prompt: str, **kwargs: Any) -> str
measure_real_vram_snapshot(self) -> list[dict[str, int]]
```

El constructor utiliza `OllamaProvider` si no se inyecta otro objeto para pruebas.
La propiedad de solo lectura `active_model: str | None` expone el último nombre
activo verificado, con etiqueta normalizada. Es un estado observado, no una
garantía de residencia permanente: cada petición vuelve a consultar al proveedor.

`route` resuelve el rol con `config.get_model_assignment`, admite únicamente el
provider `ollama` y devuelve el texto del modelo. Un provider distinto lanza
`UnsupportedProviderError`, derivado de `ValueError`, indicando la Fase 9 antes
de efectuar llamadas HTTP. Un rol desconocido o configuración inválida produce
`ConfigurationError`; los argumentos de generación inválidos producen `ValueError`.

La política de VRAM Monogamy garantiza, dentro de las operaciones de EON:

1. Un `RLock` compartido serializa las operaciones de todos los routers y
   proveedores Ollama del mismo proceso, incluida la generación.
2. Antes de cambiar de modelo se descarga explícitamente el anterior activo de
   EON, se verifica su ausencia y se exige un inventario vacío antes de cargar otro.
3. Solo se genera después de comprobar que existe exactamente un residente y
   corresponde al solicitado. Se comprueba otra vez tras la generación.
4. Si hay un modelo externo distinto al solicitado, una residencia múltiple o un
   inventario desconocido, la solicitud se cancela. No se descargan modelos ajenos
   automáticamente. Un residente único coincidente puede reutilizarse y pasa a ser
   el modelo activo gestionado por el router.
5. Una carga no confirmada activa una limpieza del modelo solicitado. Si el
   estado no se puede confirmar, nuevas cargas quedan sujetas a las mismas
   comprobaciones conservadoras de residencia.

Los fallos operativos se registran y `route` devuelve `""` para que el proceso
principal continúe. Una cadena vacía no acredita una respuesta exitosa; los
errores de configuración explícitos deben atenderse por quien llame al router.

Cada evento de carga/descarga gestionado por el router registra timestamp UTC
ISO 8601, evento, rol solicitado, nombre de modelo, resultado y snapshot de VRAM.
El logger estándar es `core.model_router`; no configura handlers globales ni
registra prompts. El proceso que lo consuma decide los destinos del logging.

El bloqueo coordina EON dentro de un proceso; no controla clientes externos de
Ollama ni otros procesos. Estos cambios se detectan mediante las consultas reales
de residencia; no se afirma un bloqueo global del servidor Ollama.

## 4. Telemetría de VRAM

`measure_real_vram_snapshot` ejecuta, sin shell y con timeout de 5 segundos:

```text
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

Devuelve una lista por GPU, en orden de salida, con:

```python
{"gpu_index": int, "memory_used_mib": int, "memory_total_mib": int}
```

Un fallo del ejecutable, timeout o CSV inválido se registra como advertencia y
devuelve `[]`; no interrumpe el router. Son instantáneas del consumo total de GPU,
no picos ni consumo atribuible exclusivamente a un modelo. No toman decisiones
de selección de modelos ni modifican la política de VRAM.

## 5. Configuración implementada

`config.py` carga `user_settings.json` junto a ese módulo una vez por importación
normal del proceso. Los accesores nunca releen el archivo ni suministran defaults.
Valida los cinco roles obligatorios, las parejas provider/model y los demás
valores que expone. Los errores indican en español la ruta y el formato esperado.

```python
get_model_assignment(role: str) -> dict[str, str]
get_notch_settings() -> dict[str, bool | int]
get_cost_limits() -> dict[str, float | str]
get_language() -> str
get_voice_profile() -> str
get_voice_settings() -> dict[str, str | int]
```

Los accesores de diccionarios devuelven copias. Los ajustes de notch, idioma, voz
y coste son solo datos declarativos: leerlos no implementa GUI, voz, Cost Guardian
o Secrets Vault ni activa conexiones externas.

Desde Fase 3, `voice_settings` es obligatorio y contiene exactamente estas claves:

| Clave | Tipo y valores permitidos en Fase 3 |
| --- | --- |
| `stt_model` | `str`: `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`; default elegido `base` |
| `stt_device` | `str`: exclusivamente `cpu` |
| `stt_compute_type` | `str`: exclusivamente `int8` |
| `tts_engine` | `str`: exclusivamente `piper` |
| `vad_aggressiveness` | `int` entre 0 y 3, sin aceptar booleanos |
| `sample_rate` | `int`: exclusivamente 16000 en esta fase, sin aceptar booleanos ni floats |
| `wake_word_model` | `str`: `alexa`, `hey_mycroft`, `hey_jarvis`, `hey_rhasspy`, `timer`, `weather`, IDs reales de openWakeWord 0.6.0 |

Claves ausentes, adicionales o valores inválidos producen `ConfigurationError`
al importar/cargar configuración. `get_voice_settings()` devuelve una copia
independiente. Toda la validación de Fase 1 se conserva. La frecuencia de la
tubería de voz queda limitada explícitamente a 16 kHz para mantener coherentes
arrays STT, VAD y wake-word; ampliar el rango requiere adaptar los consumidores.

## 6. Verificación

`tests/test_model_router.py` contiene tests HTTP mockeados y pruebas de configuración
y hardware simulado, independientes de Ollama. La integración real de carga y
descarga de `nomic-embed-text` se omite por defecto mediante `pytest.mark.skipif`.
Se habilita explícitamente con `EON_RUN_OLLAMA_INTEGRATION=1` y requiere Ollama
local sin modelos residentes al empezar. Su bloque `finally` fuerza la limpieza.

La prueba real acredita ese ciclo de vida, la residencia y las lecturas de VRAM.
La generación textual y el cambio entre modelos se prueban con HTTP mockeado;
no se presenta esta fase como una evaluación A/B ni como validación de todos
los modelos del inventario.

## 7. AcousticDetector

Implementado en `core/acoustic_detector.py`:

```python
AcousticDetector(sample_rate: int | None = None,
                 vad_aggressiveness: int | None = None,
                 device: int | str | None = None)
list_devices(self) -> list[dict[str, Any]]
record(self, seconds: float) -> tuple[int, Any]
is_speech(self, audio_frame: bytes, sample_rate: int) -> bool
get_input_level(self, seconds: float) -> float
pcm_rms(audio_frame: bytes) -> float  # staticmethod
iter_frames(self, frame_ms: int = 30,
            stop_event: threading.Event | None = None,
            timeout_seconds: float | None = None) -> Iterator[bytes]
```

- Defaults de configuración: 16000 Hz, mono, PCM int16 y VAD 2. El constructor
  explícito admite frecuencias WebRTC de 8000/16000/32000/48000 Hz y VAD 0–3.
  Parámetros inválidos producen `ValueError` antes de acceder al micrófono.
- `list_devices` consulta PortAudio real y añade `index` a cada diccionario;
  devuelve `[]` si no hay dispositivos o la consulta falla, con logging del fallo.
- `record` devuelve `(sample_rate, ndarray)` mono int16 con exactamente
  `round(seconds * sample_rate)` muestras, al menos una. Duración inválida lanza
  `ValueError`. Fallos de captura, falta de micrófono, cola saturada o captura
  incompleta devuelven un array vacío, nunca audio parcial presentado como válido.
- `is_speech` solo evalúa PCM mono int16 de 10/20/30 ms con WebRTC VAD.
  Entrada inválida o fallo de VAD se registra y devuelve `False`.
- RMS se calcula en float64, normalizado por 32768, sin overflow de int16.
  `get_input_level` devuelve 0 ante captura fallida; debe consultarse `last_error`
  para distinguirla del silencio. `pcm_rms` devuelve 0 para PCM vacío y lanza
  `ValueError` si no recibe bytes con muestras completas.
- `iter_frames` utiliza una única sesión `sounddevice.InputStream`, callback
  y cola de 50 bloques; admite 10/20/30 ms y bloques de 80 ms para wake-word.
  La cancelación y el deadline se comprueban con esperas de hasta 100 ms;
  un segundo sin audio se trata como fallo controlado. El timeout mide el
  periodo de escucha después de abrir el dispositivo, no su latencia de apertura.
- Un lock global impide dos capturas simultáneas de EON en el mismo proceso;
  una segunda captura falla de forma controlada. No reserva el micrófono frente
  a otros programas. Cerrar el generador libera stream y lock, incluso ante error.
- `last_error: str | None` expone el último fallo operativo de captura o VAD.
  No se almacenan grabaciones ni se reproduce audio automáticamente.

## 8. WakeWordDetector

En el mismo archivo `core/acoustic_detector.py`:

```python
WakeWordDetector(model_name: str, *,
                 acoustic_detector: AcousticDetector | None = None,
                 model_directory: str | Path | None = None,
                 threshold: float = 0.5)
available_models() -> list[str]  # staticmethod
is_available(self) -> bool
listen_once(self, timeout_seconds: float) -> bool
```

El constructor valida el ID contra el catálogo instalado y carga únicamente
ese modelo, más los extractores melspectrogram/embedding de openWakeWord.
Los tres son ONNX con `CPUExecutionProvider` exclusivo, comprobado tras la carga.
No usa CUDA, TFLite ni modelos de Ollama, y no descarga archivos implícitamente.
Por defecto busca en `.runtime/voice_models/wake_word`; un modelo ausente o
ilegible se registra y deja `is_available() == False`. ID desconocido, umbral
inválido o detector acústico distinto de 16 kHz producen `ValueError`.

`available_models` enumera IDs built-in del paquete; no afirma que todos sus
archivos estén descargados. El provisional elegido es `hey_jarvis`, no «Hey Eon».
`listen_once` serializa la escucha, reinicia los buffers y consume PCM en bloques
de 80 ms a 16 kHz hasta superar el umbral o vencer el timeout. Devuelve `True`
solo ante detección; silencio, fallo operativo o modelo no disponible devuelven
`False`, con `last_error` y logging para diferenciar fallo de ausencia de detección.
Timeout inválido produce `ValueError`. El cierre del generador libera el micrófono.

## 9. VoiceEngine

Implementado en `core/voice_engine.py`:

```python
VoiceEngine(*, model_directory: str | Path | None = None)
load_stt(self) -> bool
unload_stt(self) -> bool
is_stt_loaded(self) -> bool
transcribe(self, audio_input: Any) -> str
load_tts(self) -> bool
unload_tts(self) -> bool
is_tts_loaded(self) -> bool
synthesize(self, text: str, output_path: str | None = None) -> str
```

STT y TTS son lazy: no se cargan en `__init__`. Un `RLock` serializa cargas,
inferencias y descargas de una instancia; unload espera a una inferencia en curso.
Las cargas son idempotentes y solo confirman éxito tras verificar CPU. Las
descargas liberan la referencia y ejecutan garbage collection; no afirman
reservar/liberar memoria global de otros consumidores.

- STT: faster-whisper, modelo `base` elegido, `device="cpu"`, `compute_type="int8"`.
  Solo carga desde la caché local preparada en `.runtime/voice_models/whisper`
  (`local_files_only=True`); no descarga modelos durante inferencia. Comprueba
  que CTranslate2 informa CPU. `transcribe` admite únicamente rutas a WAV PCM
  sin comprimir (8/16/24/32 bits), propios o de terceros; los lee con `wave`
  estándar, no con PyAV. Rechaza MP3, OGG, WAV comprimido, WAV float y objetos
  file-like, con fallo controlado. También acepta arrays int16 o float finito
  normalizado entre -1 y 1 y buffers bytes/bytearray/memoryview PCM int16
  little-endian. Arrays/buffers sin frecuencia explícita se asumen a 16000 Hz;
  `(sample_rate: int, samples)` acepta directamente el resultado de `record`,
  incluso a otra frecuencia. Arrays de canales tienen forma (muestras, canales).
  Un int16 se divide entre 32768.0; canales se promedian a mono. Se remuestrea
  a 16000 Hz mediante interpolación lineal NumPy básica, sin filtro antialias,
  suficiente para esta vía STT pero no de calidad hi-fi. Whisper recibe SIEMPRE
  un ndarray mono float32, nunca rutas ni file-like; no se invoca `decode_audio`.
  WAV vacío/corrupto/truncado, tipos o frecuencias inválidos y audio no finito
  devuelven `""` con logging y `last_error`, antes de cargar el modelo.
  El soporte futuro de formatos comprimidos exige resolver explícitamente
  la incompatibilidad PyAV/faster-whisper, con una versión y wheel realmente
  validadas para el Python en uso, en una decisión separada y documentada.
  Se fuerza `language="es"`, beam 5, y se consumen los segmentos lazy dentro
  del bloque de errores. Devuelve texto completo o `""` ante fallo controlado;
  silencio también puede producir `""` sin error. Consultar `last_error`.
- TTS: Piper con `use_cuda=False` y sesión exclusivamente CPU verificada.
  Carga el ONNX y su JSON desde `.runtime/voice_models/piper`; la voz actual es
  `es_ES-davefx-medium`. Un ID español de voz mal formado produce `ValueError`
  en el constructor; la falta del archivo deja `load_tts() == False` con logging.
  `synthesize` genera WAV real mono PCM int16 y devuelve su ruta absoluta.
  Default: `.runtime/tts_output.wav`. Usa temporal en la misma carpeta, valida
  formato y muestras y publica mediante `os.replace`; un fallo conserva el
  destino anterior y trata de retirar el temporal. Texto vacío, ruta no WAV,
  fallo de modelo, síntesis o disco devuelve `""`, con logging y `last_error`.
- Las operaciones de carga/descarga/transcripción/síntesis registran timestamp
  UTC, operación, dispositivo CPU, resultado y duración. No registran el contenido
  de transcripciones por defecto. No implementa reproducción ni conexión GUI.

## 10. BargeInDetector

Implementado en `core/barge_in.py`:

```python
BargeInDetector(acoustic_detector: AcousticDetector | None = None, *,
                energy_threshold: float = 0.02, consecutive_frames: int = 3)
start_monitoring(self) -> None
stop_monitoring(self) -> None
is_interrupted(self) -> bool
```

`start_monitoring` inicia como máximo un hilo de captura de 30 ms y retorna sin
esperar detecciones. Un nuevo ciclo limpia el latch; iniciar mientras el hilo
está vivo es idempotente. Detecta solo cuando VAD y energía superan los criterios
durante tres bloques consecutivos por defecto. Un bloque que no cumple reinicia
el contador. La interrupción queda memorizada en un `Event` y puede consultarse
sin bloquear. Tras detectar, el monitor cierra el stream y termina.

`stop_monitoring` pide cancelación y hace join de hasta dos segundos, sin
mantener el lock de estado. Es idempotente. Fallos de hilo, captura o VAD se
registran, exponen en `last_error` y no se propagan al proceso principal; un hilo
que excede el plazo se conserva para no iniciar un segundo monitor inadvertido.
Umbral fuera de (0, 1], número de bloques no positivo o booleanos en esos
parámetros producen `ValueError`.

Detecta interrupción acústica; no cancela reproducción, no identifica hablantes
y no tiene cancelación de eco. Puede detectar audio del altavoz de EON como voz.
No conecta con GUI, safety ni Voice Fingerprint Lock.

## 11. Verificación de Fase 3

Los tres módulos tienen tests separados sin micrófono, usando `unittest.mock`.
Las nuevas validaciones de configuración se prueban en `test_voice_engine.py`.
`test_voice_integration.py` contiene un único test real, omitido por defecto
salvo `EON_RUN_VOICE_INTEGRATION=1`. Enumera micrófonos, graba 3 s y aplica VAD,
genera el WAV fijo, lo transcribe con STT CPU y escucha el wake-word 2 s.
Comprueba providers CPU, snapshots de VRAM basal antes/después y Ollama vacío;
no carga modelos de Ollama. Libera STT/TTS en `finally` y deja la evidencia en
`.runtime/fase3_voice_integration_corrected.json` para la repetición autorizada
tras corregir el decodificador; la evidencia original fallida se conserva en
`.runtime/fase3_voice_integration.json`. El WAV de muestra queda en
`.runtime/fase3_tts_sample.wav` para escucha humana, sin reproducirse solo.
