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
