# Fase 2 — Benchmark objetivo de candidatos a brain/dispatcher

Fecha de inicio (UTC): 2026-10-07T16:08:23.307663+00:00. Estado: completo.

Llamadas reales a `router.route()`: 90/90. Tiempo total: 328.750 s (5.48 min). Suma de latencias de las llamadas: 322.412 s.

## Método y límites

- Dataset fijo: 30 casos, cinco por ruta. SHA-256: `e73c85d80d0649a9480e9cca042d3c68def60ede413713ea0ab551f31c3a46bf`.
- Script ejecutado SHA-256: `068c8b7ba6cea36e3ed7b9b5c5b301b7231a5ba1b238695628b7dd1f4d4a63ad`. Python: `3.14.8`.
- Generador del informe SHA-256: `e7f66953c0976444223cd65561c6c0522fb941ec46096100645610aeaaac400d`. Si difiere del script ejecutado, solo se regeneró la presentación desde la evidencia original; no se repitieron llamadas ni se alteraron medidas.
- Orden fijo: qwen3:8b, llama3.1:latest, gemma2:9b; casos en el orden del dataset; una ejecución por pareja, sin reintentos ni calentamiento previo.
- Parámetros idénticos: `{"num_ctx": 2048, "num_predict": 1024, "seed": 42, "temperature": 0}`; HTTP local, timeout conexión/lectura 3/120 s, keep_alive 10m.
- No se fuerza `format=json`, no se repara la salida y no se altera `think`: cada modelo conserva su modo de pensamiento predeterminado. El límite común de 1024 tokens puede afectar de forma distinta a modelos con pensamiento interno.
- La primera latencia de cada modelo incluye su carga por el router; el resto reutiliza su residencia. Se incluyen los fallos en la precisión y las latencias.
- JSON inválido (%) incluye errores sintácticos y de esquema (claves exactas, ruta permitida y reasoning textual no vacío). Una respuesta vacía por fallo técnico se cuenta separadamente, no como JSON inválido.
- Precisión total = aciertos/30; precisión needs_clarification = aciertos/5. VRAM media = media de la suma de memory_used_mib de las GPU en cada instantánea disponible tras la llamada, con el modelo residente cuando se confirma la carga. No mide picos ni memoria exclusiva del modelo.
- El evento de carga del router también conserva su snapshot real. Un fallo de telemetría no se sustituye por cero; se indica N/D si no existen mediciones.
- Se reutilizan ModelRouter y OllamaProvider reales sin editar sus archivos. La asignación brain se sustituye temporalmente solo en el getter del proceso de diagnóstico y se restaura; user_settings.json no cambia.
- Se clasifica intención textual; no se ejecutan tareas especializadas. Los adjuntos mencionados se consideran disponibles por instrucción común. Es una muestra pequeña y explícita, no una validación de autonomía, seguridad ni generalización a producción.

### Entorno e inventario de modelos

```text
ollama version is 0.40.0
NAME                       ID              SIZE      MODIFIED     
bge-m3:latest              790764642607    1.2 GB    19 hours ago    
gemma3:1b-it-qat           b491bd3989c6    1.0 GB    19 hours ago    
llava:7b                   8dd30f6b0cb1    4.7 GB    21 hours ago    
phi4:latest                ac896e5b8b34    9.1 GB    21 hours ago    
gemma2:9b                  ff02c3702f32    5.4 GB    21 hours ago    
deepseek-r1:7b             755ced02ce7b    4.7 GB    21 hours ago    
nomic-embed-text:latest    0a109f422b47    274 MB    2 days ago      
qwen2.5-coder:7b           dae161e27b0e    4.7 GB    5 days ago      
psicologo:latest           6f0efbb2e496    4.7 GB    2 weeks ago     
llama3.1:latest            46e0c10c039e    4.9 GB    2 weeks ago     
qwen3:8b                   500a1f067a9f    5.2 GB    4 months ago
name, driver_version, memory.used [MiB], memory.total [MiB]
NVIDIA GeForce RTX 4060 Laptop GPU, 592.82, 8 MiB, 8188 MiB
```

### Prompt de sistema idéntico

```text
Eres el clasificador de intenciones del rol brain de EON. No ejecutes la petición ni respondas a su contenido: elige solamente su destino.
Rutas permitidas:
- vision: interpretar imágenes, capturas, gráficos o texto contenido en una imagen.
- coding: escribir, corregir o probar código, consultas SQL o expresiones regulares.
- reasoning_auditor: auditar argumentos, planes, cálculos, evidencia o coherencia de requisitos que requieren revisión razonada.
- embeddings: producir vectores semánticos para indexación o similitud, no explicar el concepto.
- direct_answer: consultas simples que pueden responderse directamente, sin análisis especializado.
- needs_clarification: faltan el objetivo, el referente, el destinatario, el archivo o el contexto necesarios para actuar; solicita confirmación humana en lugar de inventarlos.
Para este ejercicio, una imagen o documento descrito explícitamente como adjunto se considera disponible: solo clasificas la intención, no inspeccionas el adjunto. No supongas contexto previo que no esté en la entrada.
Responde ÚNICAMENTE con un objeto JSON válido, sin Markdown ni texto adicional, con exactamente dos claves: {"route": "<una de las seis rutas>", "reasoning": "una frase breve en español explicando la elección"}.
```

## Tabla comparativa

| Modelo | Precisión total (%) | Precisión needs_clarification (%) | Latencia media (s) | Latencia máxima (s) | VRAM media observada (MiB) | JSON inválido (%) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen3:8b | 96.67 | 100.00 | 7.246 | 25.749 | 5167.17 | 0.00 |
| llama3.1:latest | 83.33 | 60.00 | 1.296 | 11.367 | 4886.53 | 3.33 |
| gemma2:9b | 26.67 | 0.00 | 2.205 | 39.357 | 6090.33 | 63.33 |

### Recuentos y carga inicial

| Modelo | Intentos | Aciertos | Aciertos aclaración | Fallos formato | Fallos técnicos | Primera llamada (s) | Snapshots disponibles | VRAM en evento de carga (MiB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| qwen3:8b | 30 | 29/30 | 5/5 | 0 | 0 | 25.749 | 30 | 5439 |
| llama3.1:latest | 30 | 25/30 | 3/5 | 1 | 0 | 11.367 | 30 | 5139 |
| gemma2:9b | 30 | 8/30 | 0/5 | 19 | 0 | 39.357 | 30 | 6095 |

## Tabla de errores

Cada fila corresponde a un caso con al menos un fallo; se muestra la respuesta de los tres candidatos frente a la ruta esperada.

| Caso | Prompt | Ruta esperada | qwen3:8b | llama3.1:latest | gemma2:9b |
| ---: | --- | --- | --- | --- | --- |
| 1 | Describe los objetos que aparecen en la foto adjunta. | vision | vision (acierto) | vision (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 2 | Escribe una función en Python que elimine duplicados de una lista conservando el orden. | coding | coding (acierto) | coding (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 3 | Revisa este argumento: todos los gatos son mamíferos y todos los perros son mamíferos, por tanto todos los gatos son perros. Identifica el fallo lógico. | reasoning_auditor | reasoning_auditor (acierto) | reasoning_auditor (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 4 | Genera los embeddings de este texto para indexarlo: «EON funciona primero en local». | embeddings | embeddings (acierto) | embeddings (acierto) | direct_answer (error) |
| 5 | ¿Cuántos minutos tiene una hora? | direct_answer | direct_answer (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) | direct_answer (acierto) |
| 6 | Borra eso. | needs_clarification | needs_clarification (acierto) | direct_answer (error) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 7 | Lee el texto del cartel que aparece en la imagen adjunta. | vision | vision (acierto) | vision (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 8 | Escribe un test de pytest para comprobar que sumar(2, 3) devuelve 5. | coding | coding (acierto) | direct_answer (error) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 9 | Audita este plan de copias de seguridad: guardar la única copia en el mismo disco que los originales. Explica sus riesgos y los supuestos que faltan. | reasoning_auditor | reasoning_auditor (acierto) | reasoning_auditor (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 10 | Convierte estas dos frases en vectores para medir su similitud semántica: «abre la ventana» y «cierra la ventana». | embeddings | embeddings (acierto) | embeddings (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 12 | Envíaselo a él. | needs_clarification | needs_clarification (acierto) | needs_clarification (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 13 | Compara las dos capturas adjuntas e identifica qué botones cambiaron de posición. | vision | vision (acierto) | vision (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 15 | Comprueba paso a paso si este cálculo es correcto: tres unidades de 19 euros con un descuento del 10 % cuestan 51,30 euros. | reasoning_auditor | reasoning_auditor (acierto) | reasoning_auditor (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 16 | Calcula un embedding para la consulta «política de copias de seguridad» que usaré en una búsqueda vectorial. | embeddings | embeddings (acierto) | embeddings (acierto) | direct_answer (error) |
| 18 | Cambia la configuración para que vaya mejor. | needs_clarification | needs_clarification (acierto) | coding (error) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 21 | Evalúa si se sostiene esta conclusión: probamos un programa una vez sin errores, así que nunca puede fallar. Explica qué evidencia adicional haría falta. | reasoning_auditor | reasoning_auditor (acierto) | reasoning_auditor (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 22 | Vectoriza por separado estos tres documentos para indexarlos: «manual de instalación», «guía de usuario» y «registro de cambios». | embeddings | needs_clarification (error) | embeddings (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 24 | Ejecuta el comando que te dije antes. | needs_clarification | needs_clarification (acierto) | needs_clarification (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 25 | Analiza el gráfico de barras de la imagen adjunta y dime qué barra es la más alta. | vision | vision (acierto) | vision (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 26 | Crea una expresión regular que valide una cadena formada exactamente por cinco dígitos. | coding | coding (acierto) | coding (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 27 | Revisa la coherencia de estos requisitos: la aplicación debe funcionar siempre sin red y todas sus respuestas deben obtenerse de un servicio remoto. Señala las contradicciones. | reasoning_auditor | reasoning_auditor (acierto) | needs_clarification (error) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |
| 28 | Genera un vector semántico del párrafo «La interfaz permite consultar documentos locales» para guardarlo en el índice de búsqueda. | embeddings | embeddings (acierto) | embeddings (acierto) | direct_answer (error) |
| 30 | Abre el archivo. | needs_clarification | needs_clarification (acierto) | needs_clarification (acierto) | Sin ruta; fallo de formato: JSON inválido: Expecting value: line 1 column 1 (char 0) |

### Salidas completas con fallos de formato

#### llama3.1:latest — caso 5

~~~~text
```json
{
  "route": "direct_answer",
  "reasoning": "La pregunta es una consulta simple que puede responderse directamente sin análisis especializado."
}
```
~~~~

#### gemma2:9b — caso 1

~~~~text
```json
{"route": "vision", "reasoning": "Se solicita describir objetos en una imagen, lo cual implica procesamiento visual."}
```
~~~~

#### gemma2:9b — caso 2

~~~~text
```json
{"route": "coding", "reasoning": "Se solicita escribir código para eliminar duplicados de una lista."}
```
~~~~

#### gemma2:9b — caso 3

~~~~text
```json
{"route": "reasoning_auditor", "reasoning": "El argumento presenta una falacia lógica, se debe identificar el error en la deducción."}
```
~~~~

#### gemma2:9b — caso 6

~~~~text
```json
{"route": "vision", "reasoning": "Se requiere la interpretación de contenido visual."}
```
~~~~

#### gemma2:9b — caso 7

~~~~text
```json
{"route": "direct_answer", "reasoning": "El cartel contiene información que puede responderse directamente."}
```
~~~~

#### gemma2:9b — caso 8

~~~~text
```json
{"route": "coding", "reasoning": "Se solicita escribir código para probar una función."}
```
~~~~

#### gemma2:9b — caso 9

~~~~text
```json
{"route": "reasoning_auditor", "reasoning": "Se necesita auditar un plan de copias de seguridad, identificando riesgos y supuestos faltantes."}
```
~~~~

#### gemma2:9b — caso 10

~~~~text
```json
{"route": "direct_answer", "reasoning": "Es una consulta simple que puede responderse directamente."}
```
~~~~

#### gemma2:9b — caso 12

~~~~text
```json
{"route": "direct_answer", "reasoning": "La pregunta puede responderse directamente sin análisis especializado."}
```
~~~~

#### gemma2:9b — caso 13

~~~~text
```json
{"route": "vision", "reasoning": "Se requiere comparar imágenes para identificar cambios en la posición de los botones."}
```
~~~~

#### gemma2:9b — caso 15

~~~~text
```json
{"route": "reasoning_auditor", "reasoning": "Se requiere verificar la precisión de un cálculo con descuento."}
```
~~~~

#### gemma2:9b — caso 18

~~~~text
```json
{"route": "direct_answer", "reasoning": "La pregunta puede responderse directamente sin análisis especializado."}
```
~~~~

#### gemma2:9b — caso 21

~~~~text
```json
{"route": "reasoning_auditor", "reasoning": "Se requiere analizar la lógica del argumento y determinar si es válido."}
```
~~~~

#### gemma2:9b — caso 22

~~~~text
```json
{"route": "vision", "reasoning": "Se solicita la interpretación de documentos, lo que implica procesar información visual."}
```
~~~~

#### gemma2:9b — caso 24

~~~~text
```json
{"route": "direct_answer", "reasoning": "La solicitud es una pregunta simple que puede responderse directamente."}
```
~~~~

#### gemma2:9b — caso 25

~~~~text
```json
{"route": "vision", "reasoning": "Se requiere analizar un gráfico de barras para determinar la barra más alta."}
```
~~~~

#### gemma2:9b — caso 26

~~~~text
```json
{"route": "coding", "reasoning": "Se solicita la creación de una expresión regular."}
```
~~~~

#### gemma2:9b — caso 27

~~~~text
```json
{"route": "needs_clarification", "reasoning": "Se requiere información adicional para determinar la acción a realizar."}
``` 

~~~~

#### gemma2:9b — caso 30

~~~~text
```json
{"route": "vision", "reasoning": "Se solicita la interpretación de un contenido visual."}
```
~~~~


## Muestras completas de reasoning

### qwen3:8b

Caso 1 — esperada `vision`, obtenida `vision`:

"La solicitud requiere interpretar el contenido visual de una imagen para describir los objetos presentes."

Caso 6 — esperada `needs_clarification`, obtenida `needs_clarification`:

"La solicitud no especifica qué contenido o elemento debe ser borrado, faltando contexto para actuar"

Caso 9 — esperada `reasoning_auditor`, obtenida `reasoning_auditor`:

"El plan propuesto carece de redundancia y asume la fiabilidad del disco sin considerar fallos, requiriendo análisis de riesgos y supuestos críticos para una estrategia de respaldo efectiva."

Caso 16 — esperada `embeddings`, obtenida `embeddings`:

"El usuario solicita generar un vector semántico para una consulta de búsqueda vectorial, lo cual corresponde a la ruta de embeddings."

Caso 23 — esperada `direct_answer`, obtenida `direct_answer`:

"La pregunta solicita una información lingüística básica que puede responderse directamente sin análisis adicional."

### llama3.1:latest

Caso 1 — esperada `vision`, obtenida `vision`:

"La petición solicita la interpretación de una imagen, lo que corresponde a la ruta de visión."

Caso 6 — esperada `needs_clarification`, obtenida `direct_answer`:

"La petición es una orden directa que se puede responder con un simple sí o no."

Caso 9 — esperada `reasoning_auditor`, obtenida `reasoning_auditor`:

"El plan de copias de seguridad propuesto tiene un riesgo significativo de pérdida de datos en caso de un fallo del disco o un incidente de seguridad en el servidor, ya que la única copia se encuentra en el mismo disco que los originales. Además, faltan supuestos sobre la frecuencia de copia de seguridad, el tipo de datos que se están protegiendo y la disponibilidad de recursos para restaurar los datos en caso de una falla."

Caso 16 — esperada `embeddings`, obtenida `embeddings`:

"La petición solicita un vector semántico para la consulta, lo que indica que se trata de una búsqueda de similitud o indexación."

Caso 23 — esperada `direct_answer`, obtenida `direct_answer`:

"La respuesta es un término común y su plural es conocido por la mayoría de las personas."

### gemma2:9b

Caso 16 — esperada `embeddings`, obtenida `direct_answer`:

"La consulta es una pregunta simple que puede responderse directamente."

Caso 23 — esperada `direct_answer`, obtenida `direct_answer`:

"La pregunta tiene una respuesta directa y simple."

Caso 4 — esperada `embeddings`, obtenida `direct_answer`:

"Es una afirmación simple que puede responderse directamente."

Caso 5 — esperada `direct_answer`, obtenida `direct_answer`:

"Es una pregunta simple con respuesta directa."

Caso 11 — esperada `direct_answer`, obtenida `direct_answer`:

"Es una traducción simple de una frase."

## Problemas técnicos y aislamiento entre modelos

No se observaron timeouts ni fallos técnicos de carga o generación en las llamadas ejecutadas.

Antes de qwen3:8b (2026-10-07T16:08:23.413869+00:00): /api/ps vacío y salida literal de ollama ps:

```text
NAME    ID    SIZE    PROCESSOR    CONTEXT    RUNNER    UNTIL
```

Después de qwen3:8b (2026-10-07T16:12:02.960653+00:00): /api/ps vacío y salida literal de ollama ps:

```text
NAME    ID    SIZE    PROCESSOR    CONTEXT    RUNNER    UNTIL
```

Antes de llama3.1:latest (2026-10-07T16:12:03.000265+00:00): /api/ps vacío y salida literal de ollama ps:

```text
NAME    ID    SIZE    PROCESSOR    CONTEXT    RUNNER    UNTIL
```

Después de llama3.1:latest (2026-10-07T16:12:43.859909+00:00): /api/ps vacío y salida literal de ollama ps:

```text
NAME    ID    SIZE    PROCESSOR    CONTEXT    RUNNER    UNTIL
```

Antes de gemma2:9b (2026-10-07T16:12:43.900052+00:00): /api/ps vacío y salida literal de ollama ps:

```text
NAME    ID    SIZE    PROCESSOR    CONTEXT    RUNNER    UNTIL
```

Después de gemma2:9b (2026-10-07T16:13:52.101052+00:00): /api/ps vacío y salida literal de ollama ps:

```text
NAME    ID    SIZE    PROCESSOR    CONTEXT    RUNNER    UNTIL
```

## Alcance de la conclusión

Este informe solo aporta medidas y respuestas observadas. No selecciona ganador, no fija una asignación de brain y no modifica user_settings.json ni DECISIONS.md. La decisión corresponde a Pablo.
