# EON — Contratos congelados de Fase 1

Fecha: 2026-10-07. Implementación: `config.py` y `core/model_router.py`.
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
```

Los accesores de diccionarios devuelven copias. Los ajustes de notch, idioma, voz
y coste son solo datos declarativos: leerlos no implementa GUI, voz, Cost Guardian
o Secrets Vault ni activa conexiones externas.

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
