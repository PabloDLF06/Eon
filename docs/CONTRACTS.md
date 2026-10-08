# EON — Contratos congelados de Fases 1, 3 y 4

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
get_voice_settings() -> dict[str, str | int | float | dict[str, str]]
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
| `tts_speaker_id` | `int` >= 0, sin booleanos; elegido 0 (`M`) |
| `tts_length_scale` | `int` o `float` finito entre 0.5 y 2.0 inclusive, sin booleanos; elegido 1.15 |
| `tts_noise_scale` | `int` o `float` finito entre 0.0 y 2.0 inclusive, sin booleanos; elegido 0.7337 |
| `tts_noise_w_scale` | `int` o `float` finito entre 0.0 y 2.0 inclusive, sin booleanos; elegido 0.88 |
| `tts_pronunciation_aliases` | `dict[str, str]`: claves y valores no vacíos, sin espacios externos; elegido `{"Eon": "Eeeón", "EON": "Eeeón"}`; `{}` desactiva los alias |

Claves ausentes, adicionales o valores inválidos producen `ConfigurationError`
al importar/cargar configuración. `get_voice_settings()` devuelve una copia
profunda independiente, incluidos los alias anidados. El cero en los parámetros
de ruido es válido (sin variabilidad de ese componente). `voice_profile` es un
ID real de voz Piper, actualmente `es_ES-sharvard-medium`; no es un nombre libre
ni un alias visual. Toda la validación de Fase 1 se conserva. La frecuencia de la
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
  `es_ES-sharvard-medium`. Un ID español de voz mal formado produce `ValueError`
  en el constructor; la falta del archivo deja `load_tts() == False` con logging.
  Un speaker fuera del rango de hablantes del modelo deja `load_tts() == False`
  con logging y `last_error`. `synthesize` pasa los ajustes validados mediante
  `SynthesisConfig(speaker_id=0, length_scale=1.15, noise_scale=0.7337,
  noise_w_scale=0.88)` en la configuración elegida, manteniendo los defaults
  reales de Piper `normalize_audio=True` y `volume=1.0`.
  Los alias se aplican una sola vez, con coincidencias literales sensibles a
  mayúsculas y límites de palabra Unicode; no alteran partes internas de otras
  palabras ni encadenan sustituciones. Solo transforman una copia local del texto
  que se envía a Piper: no cambian el nombre visible EON/Eon, el texto original,
  las rutas de salida, los logs de EON ni el contenido recibido por otros módulos.
  Se pasa el texto completo en una llamada; Piper lo procesa por frases internas.
  `synthesize` genera WAV real mono PCM int16 y devuelve su ruta absoluta.
  Default: `.runtime/tts_output.wav`. Usa temporal en la misma carpeta, valida
  formato y muestras y publica mediante `os.replace`; un fallo conserva el
  destino anterior y trata de retirar el temporal. Texto vacío, ruta no WAV,
  fallo de modelo, síntesis o disco devuelve `""`, con logging y `last_error`.
- Las operaciones de carga/descarga/transcripción/síntesis registran timestamp
  UTC, operación, dispositivo CPU, resultado y duración. No registran el contenido
  de transcripciones por defecto. No implementa reproducción ni conexión GUI.

La naturalidad de Piper fue aceptada por Pablo para el cierre funcional de
Fase 3, no como calidad definitiva. Persiste cierta artificialidad/trompiconeo;
la mejora avanzada de prosodia/voz queda como deuda de UX/pulido fuera de esta fase.

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

## 12. EonState y paleta — Fase 4, 2026-10-08

En core/eon_state.py, sin importar PyQt6:

```python
class EonState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    BUILDING = "building"
    ERROR = "error"
    VISION_ACTIVE = "vision_active"

state_color(state: EonState) -> str
```

STATE_COLORS y STATE_LABELS son mapas de solo lectura, completos para los siete
estados. state_color rechaza valores ajenos a EonState con ValueError. Colores:
IDLE #8ea9c7, LISTENING #00e5ff, THINKING #a742ff, SPEAKING #ff3de0,
BUILDING #ff9f1c, ERROR #ff3b3b y VISION_ACTIVE #39ff88. El último es una
propuesta provisional pendiente de confirmación visual de Pablo. Los labels
están en español; el estado no se comunica únicamente por color.

## 13. NotchController y adaptador Qt — Fase 4, 2026-10-08

gui/notch_window.py expone la lógica pura sin importar PyQt6 ni requerir
QApplication. Solo solicitar NotchWindow importa perezosamente el adaptador
QWidget de gui/notch_qt.py. Importar EonState o NotchController funciona incluso
si PyQt6 no está disponible.

```python
class NotchGeometryState(Enum):
    PEEK = "peek"
    HOVER_PEEK = "hover_peek"
    EXPANDED = "expanded"

NotchController(clock: Callable[[], float] = time.monotonic)
set_eon_state(self, state: EonState) -> None
notify_voice_activity(self, active: bool) -> None
notify_draft_activity(self, active: bool) -> None
notify_vision_active(self, active: bool) -> None
on_mouse_enter(self) -> None
on_mouse_leave(self) -> None
on_click(self) -> None
expand(self) -> None
collapse(self) -> None
tick(self) -> None
```

Propiedades de solo lectura: eon_state: EonState, geometry_state:
NotchGeometryState, voice_active: bool, draft_active: bool y vision_active: bool.
Estado inicial: IDLE, PEEK, tres flags False y ratón fuera. El constructor toma
una copia de config.get_notch_settings(); no añade ajustes al JSON ni implementa
recarga dinámica. El reloj debe ser invocable, finito y monótono; un reloj inválido
produce ValueError. Las notificaciones exigen bool real y set_eon_state exige
EonState; errores de uso producen ValueError antes de cambiar su estado.

- on_mouse_enter lleva PEEK a HOVER_PEEK; no colapsa EXPANDED.
- on_mouse_leave lleva una vista previa sin clic a PEEK; un panel expandido
  sigue expandido y comienza un nuevo intervalo si es elegible.
- on_click y expand fuerzan EXPANDED sin cambiar actividad lógica.
- collapse fuerza PEEK, salvo mientras vision_active sea True.
- notify_vision_active(True) fuerza EXPANDED inmediatamente y tick mantiene
  esa prioridad sobre colapso, hover y auto-hide, incluso con auto-hide desactivado.
- Voz y borrador solo bloquean auto-hide; no fuerzan expansión ni alteran EonState.
  El flag vision_active y el estado lógico VISION_ACTIVE son independientes.
- Solo cuenta un intervalo continuo cuando auto-hide está habilitado, el panel
  no está en PEEK, EonState es IDLE, los tres flags están inactivos y el ratón
  está fuera. Actividad o presencia del ratón reinicia el intervalo: al liberar
  el último bloqueo se cuentan de nuevo los N segundos completos. Notificar
  repetidamente el mismo valor no retrasa el deadline.
- tick debe llamarse periódicamente; colapsa al alcanzar N segundos, no antes.
  El controlador no utiliza QTimer, hilos ni APIs de hardware.

Estas interfaces se usarán desde voz/visión en fases futuras; todavía no hay
wiring real. Cambiar sus firmas o garantías exige una decisión documentada
previa y actualización coordinada de contratos y tests, igual que ModelProvider.
El controlador y los widgets se consumen de forma serializada desde el hilo GUI;
futuros emisores de otros hilos deberán encolar notificaciones a ese hilo.

```python
NotchWindow(controller: NotchController | None = None)  # QWidget
refresh(self, *args: object) -> None
```

NotchWindow es frameless, always-on-top, Tool y WA_TranslucentBackground.
PEEK/HOVER_PEEK usan WindowDoesNotAcceptFocus y NoFocus; EXPANDED permite foco.
Un clic en la vista reducida solo solicita expansión, sin invocar activateWindow;
un clic en el panel ya expandido puede tomar foco. Esc es colapso local, no hotkey
global. QTimer de 200 ms llama a tick y sincroniza color, visibilidad y geometría.
QScreen.geometry() calcula el centro superior actual en píxeles lógicos; observa
cambios de pantalla principal y geometría, también reevalúa cada tick y limita
el tamaño si la pantalla es menor. No usa coordenadas de pantalla fijas.

Dimensiones iniciales: PEEK 220×5, HOVER_PEEK 220×27, EXPANDED 220×90, todas
provisionales hasta revisión visual. Personaje y labels aparecen solo expandido;
la vista previa muestra texto de estado. Acceso a pantalla/cursor y eventos de
ratón se capturan con logging; last_error: str | None expone fallos periféricos.
Al cerrar se detiene QTimer y se desconectan observadores de pantalla.
Las flags y dimensiones se comprueban offscreen; foco real, barra de tareas,
composición, contraste sobre el escritorio y varios monitores requieren prueba
visual humana. Esta fase no acredita esas propiedades por observación en Windows.

## 14. CharWidget y verificación de Fase 4 — 2026-10-08

```python
CharWidget(parent: QWidget | None = None)  # QWidget
set_eon_state(self, state: EonState) -> None
```

gui/char_widget.py pinta un ojo vectorial funcional con QPainter. Es contenido
artístico temporal, no lógica incompleta: assets/char/ solo tiene .gitkeep.
set_eon_state exige EonState, actualiza el label accesible y anima QColor durante
220 ms con InOutCubic. Una transición interrumpida parte del color interpolado
actual; repetir el estado no reinicia la animación. No altera EonState global,
geometría ni flags del controlador. No se implementan sprites o arte final.
Su firma también requiere decisión previa documentada antes de modificarse.

Tests puros: test_eon_state.py y test_notch_controller.py, con reloj inyectable
y prueba de importación/ejecución en un proceso que prohíbe PyQt6. Tests mínimos
Qt: test_char_widget.py y test_notch_qt.py, con QT_QPA_PLATFORM=offscreen,
sin pytest-qt; comprueban animación, instanciación, flags, geometría y fallo
de pantalla simulado, no validación de pantalla/ratón reales. Si falta PyQt6,
solo los tests Qt se omiten; la lógica pura sigue siendo testeable.

scripts/fase4_notch_harness.py es una herramienta manual, no un test de suite.
Abre el notch inicialmente expandido y una ventana de controles con siete
estados, tres flags, expandir/colapsar y cerrar. No importa módulos de voz/visión
ni carga modelos, no captura micrófono/pantalla y no registra hotkeys globales.

## 15. Actualización exclusivamente visual de Fase 4 — 2026-10-08

La decisión 16 de DECISIONS.md actualiza el acabado descrito en los apartados
13–14, sin cambiar ninguna firma, regla ni garantía del NotchController. Los
estados, auto-hide, reloj, notificaciones, foco y dimensiones externas se conservan.
CharWidget(parent: QWidget | None = None) y set_eon_state(state: EonState) -> None
mantienen sus firmas y validación; no se añade un método público para los consumidores.

El ojo inicial se sustituye por un cuerpo vectorial ancho de superelipse, rostro
mínimo, degradado cálido/estado, glow y siete insignias geométricas originales.
Color y expresión/insignia se interpolan o disuelven durante 220 ms, retomando la
mezcla actual si se interrumpe una transición. Los controles Qt de animación son
detalles internos de pintado, no parte de la API de voz/visión.

La cápsula es siempre #121318. EonState colorea solo el personaje/glow/insignia;
los labels y el borde de foco son neutros. La ventana conserva 220×5/27/90 por
geometría, con tamaño nativo inmediato y recorte/traslación interiores animados.
PEEK/HOVER_PEEK pintan vistas parciales del mismo personaje en el padre; el
widget hijo permanece visible únicamente en EXPANDED, ahora con texto debajo.
No se amplía una región invisible permanente para capturar ratón. Este cambio
no garantiza aún fluidez/foco/composición en pantalla real; requiere revisión
visual humana. No se descargan ni incorporan activos externos o dependencias.

## 16. Ampliación del panel y configuración local — Fase 4, 2026-10-08

La decisión 17 de DECISIONS.md reemplaza las dimensiones, cápsula neutra fija
y vistas parciales históricas de las secciones 13–15. PEEK 300×16 nominal,
HOVER_PEEK 300×27 nominal, ancho cerrado proporcional 280–320; EXPANDED
460×280, padding 18. Todos limitados al área QScreen actual. El personaje
está completamente oculto y reveal_progress es 0 en ambos estados cerrados.
Tamaño nativo, opacidad y revelado se animan en paralelo; la onda radial por
estado tiene su propia animación que no se reinicia al cambiar geometría.
Las propiedades Qt de pintado/animación siguen siendo detalles internos.
CharWidget conserva sus firmas y añade solo vida/depth vectoriales propias.

Ampliación aditiva del controlador puro, sin cambiar reglas ni firmas previas:

```python
NotchController.reload_settings(self) -> None
```

Obtiene los dos ajustes validados de la caché config, preserva instancia,
geometría, EonState y flags, y reinicia el intervalo idle completo si es elegible.
No lee archivos ni importa Qt. Guardar el diálogo lo invoca en el hilo GUI.
Los accesores siguen sin releer el disco; solo un guardado explícito lo relee.

Nuevas interfaces de config.py:

```python
get_quick_launch_shortcuts() -> list[dict[str, str]]
save_notch_settings(*, auto_hide_enabled: bool, auto_hide_seconds: int,
                    shortcuts: list[dict[str, str]]) -> None
```

quick_launch_shortcuts es obligatorio; [] es válido y no crea botones.
Cada elemento contiene exactamente label/path/color, strings no vacíos y sin
controles/espacios externos. path es absoluto; se trata como una ruta literal,
no como línea de comandos. color debe ser #RRGGBB. Getter devuelve copia profunda.
Save valida todo el documento con las mismas reglas de carga, preservando los
otros campos del disco y publicando mediante temporal/fsync/os.replace. Solo
tras publicar cambia la caché. Fallos lanzan ConfigurationError en español y
se registran, manteniendo destino/cache previos y limpiando el temporal si es
posible. No implementa Secrets Vault ni una interfaz para guardar secretos.

Interfaz nueva de NotchWindow, siempre desde el hilo GUI:

```python
handle_user_message(self, text: str) -> None
open_settings(self) -> None
start_microphone_meter(self) -> None
closed  # señal Qt sin argumentos, tras cerrar y liberar la captura
```

handle_user_message exige str (otros tipos producen ValueError), ignora vacío
o solo espacios sin efectos, y para texto no vacío limpia text_input y muestra
«Mensaje recibido. La conexión con el cerebro de EON se activará en una fase
posterior.». Enter y botón Enviar llaman a esa misma función. No infiere, simula
respuesta, registra ni almacena contenido del mensaje. Su firma queda congelada
para reemplazar solo la implementación interna durante la integración futura.
El campo no vacío notifica borrador activo al controlador.

open_settings crea un único SettingsDialog no modal y funcional; abrirlo de
nuevo enfoca el mismo. Auto-hide queda bloqueado mientras está abierto. Guardar
aplica preferencias y reconstruye botones sin reiniciar ni reemplazar controlador.
El diálogo en gui/notch_controls.py expone saved (señal sin argumentos), save(),
add_shortcut(), remove_shortcut(), choose_file(), choose_directory(), choose_color().
Los selectores son reales; errores mantienen el diálogo abierto con explicación.

En gui/notch_controls.py:

```python
launch_shortcut(path: str) -> tuple[bool, str]
IconButton(kind: str, label: str, parent: QWidget | None = None)
MicrophoneLevelWorker(parent: QWidget | None = None, *, duration: float = 3.0,
                      detector_factory: Callable[[], AcousticDetector] | None = None)
MicrophoneLevelWorker.stop(self) -> None
```

Launcher comprueba existencia/ruta absoluta y usa Popen con argv y shell=False;
ejecutables .exe/.com directamente, otras rutas mediante explorer.exe en Windows
(xdg-open en otros sistemas). Captura excepciones con logging y devuelve éxito
de lanzamiento o fallo con mensaje español; el panel muestra siempre ese mensaje.
No verifica la aparición posterior de la ventana externa. IconButton acepta
gear/microphone/send; otros valores producen ValueError. Dibujo original QPainter,
tooltip/label accesible español y foco visible; ningún asset externo.

El trabajador QThread emite level(float RMS normalizado 0–1), result(str español)
y la señal finished heredada. Valida duración finita positiva sin bool. Crea
AcousticDetector solo al ejecutar, sondea get_input_level con ventanas <=120 ms
hasta plazo/cancelación. Un fallo del detector o RMS inválido se registra y
finaliza con aviso, nunca se presenta como silencio exitoso. stop pide cancelación
entre ventanas; no mata hilos ni promete controlar la latencia de PortAudio al
abrir/cerrar. El panel permite un solo trabajador, bloquea voz durante su ejecución,
restaura el flag previo, deshabilita el botón y consume señales en el hilo GUI.
Cerrar solicita cancelación y difiere el cierre hasta finished; el harness espera
closed antes de salir. No bloquea la GUI con join ni destruye un hilo activo.

Alcance: medidor de nivel de voz real, transcripción y respuesta pendientes de
fase de integración. No se invoca STT/TTS/brain ni se guarda audio. Harness ahora
permite probar configuración, apertura real de accesos y captura opt-in; los siete
botones de estados y tres flags siguen siendo simulación manual, no wiring real
de esos subsistemas. Test suites de panel/controles mockean micrófono y Popen;
offscreen no acredita escritorio, periféricos o fluidez física.

## 17. Pulido de Fase 4: forma de onda, nivel perceptivo y clic — 2026-10-08

La decisión 18 actualiza la presentación/timing de medición de la sección 16,
sin modificar NotchController ni firmas de config o handle_user_message.
PEEK 280–320×16, HOVER_PEEK mismo ancho×27, EXPANDED nominal 680×300 con
clamp pantalla−24/altura; mínimo nominal 560 condicionado al espacio físico.
Padding 18 y separación 8/12; personaje oculto/reveal 0 en modos cerrados.
Onda horizontal lineal simétrica en PEEK/hover, radial en EXPANDED. Morph de
forma 180 ms independiente del frente de estado de 620 ms y tamaño 330/290 ms.
Las propiedades Qt de animación siguen siendo detalles internos de render.

SettingsDialog conserva saved y sus métodos públicos sin cambiar validación,
selectores, guardado real, aplicación ni bloqueo de auto-hide. El QTableWidget
interno se sustituye por tarjetas; no era una API congelada para consumidores.
Selector 10/15/20 con tiempo personalizado conserva los valores existentes.
Tamaño nominal 520×560 limitado al área disponible menos 24 por dimensión.

Nuevo módulo puro gui/microphone_meter.py, sin Qt ni captura:

```python
rms_to_level(rms: float) -> float
MicrophoneMeter(*, clock: Callable[[], float] = time.monotonic)
MicrophoneMeter.update(self, rms: float) -> float
MicrophoneMeter.tick(self) -> float
MicrophoneMeter.reset(self) -> None
```

rms_to_level acepta RMS normalizado 0–1, numérico finito sin bool; datos inválidos
lanzan ValueError en español, nunca equivalen a silencio. Devuelve nivel 0–1
según clamp((20*log10(max(rms,1e-6))+50)/42,0,1). update publica level, actualiza
peak y devuelve level. tick devuelve peak con hold 300 ms y caída de 0.65/s,
limitado inferiormente a level; reset deja ambos a 0. Consumidor serializado,
reloj monotónico inyectable; no comparte estado entre hilos.

Actualización explícita del trabajador, conserva stop y señales level/result:

```python
MicrophoneLevelWorker(parent: QWidget | None = None, *, duration: float | None = None,
                      detector_factory: Callable[[], AcousticDetector] | None = None)
MicrophoneLevelWorker.last_error: str | None
```

None activa sondeo continuo hasta stop; una duración finita positiva sigue
permitiendo medición acotada. level emite RMS ORIGINAL 0–1, no dB ni porcentaje.
last_error queda None al terminar/cancelar correctamente y contiene el fallo
si detector/captura devuelve error o RMS inválido. Fallos con logging y result
español. No invoca modelos ni persiste audio. get_input_level usa ventanas de
hasta 120 ms; esto no promete stream PortAudio persistente ni latencia máxima
para apertura/cierre. Cancelación se comprueba entre esas ventanas.

NotchWindow.start_microphone_meter() conserva la firma y pasa a alternar
inicio/stop del único trabajador; el botón queda habilitado para detenerlo.
Bloquea auto-hide con actividad de voz y restaura el flag previo al terminar.
Su UI transforma RMS con rms_to_level: barra interna 0–1000, nivel público
0–1, alto 10, cyan LISTENING y marca de pico independiente. Timer de 40 ms,
solo hilo GUI. Muestra inactivo/esperando/silencio/señal/error; last_error del
trabajador nunca se presenta como silencio. Cierre diferido hasta finished
y señal closed se mantienen. STT/brain quedan pendientes, sin inferencia.

Ampliación aditiva de CharWidget:

```python
CharWidget.clicked  # señal Qt sin argumentos
```

Emite al clic izquierdo o Enter/Espacio con foco. set_eon_state conserva firma
y validación; clic solo activa recoil, y el panel presenta un bocadillo original
1.6 s, sin mutar controlador ni estado lógico. No es una respuesta inteligente.
Padding de dibujo 6 y viewport 120×76 dejan halo y cuerpo dentro del widget.
Mirada IDLE, squash, respiración y parpadeo son detalles visuales que se paran
al ocultarse. Foco de teclado visible; tabulación personaje→engranaje→accesos
→texto→micrófono→enviar. Identidad profunda del personaje es deuda de arte.

Tests correspondientes: test_microphone_meter.py (puro), test_char_widget.py,
test_notch_panel.py, test_notch_controls.py; sin micrófono ni procesos reales.
Los contratos puros previos de actividad/visión/auto-hide no cambian. Renders
propios offscreen solo comprueban composición lógica; aprobación física humana
pendiente, sin autorizar merge a main ni inicio de Fase 5.
