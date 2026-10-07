ESTADO: diseño objetivo del proyecto. Nada de esto está implementado todavía.

# EON — Especificación maestra

## 1. Alcance y desarrollo

EON se proyecta como un asistente de escritorio para Windows 11 con interfaz
flotante, voz, percepción de pantalla y autonomía supervisada. Este documento
describe objetivos, no capacidades disponibles ni pruebas realizadas.

El desarrollo se realiza exclusivamente en local, en `C:\Dev\Eon`, con Codex
Desktop en modo «Este ordenador». No existe un flujo alternativo de Codex Cloud
ni de App Web. Las fases aprobadas se recogen en [PHASES.md](PHASES.md).

## 2. Hardware objetivo y límites

Hardware comunicado por el usuario; todavía no medido por el proyecto:

- CPU: AMD Ryzen 7 7840HS, 8 núcleos y 16 hilos.
- RAM: 16 GB DDR5, con aproximadamente 3,5 GB libres en reposo.
- GPU: NVIDIA RTX 4060 Laptop, 8 GB de VRAM.
- Disco: NVMe Gen4.
- Sistema operativo: Windows 11.

La disponibilidad reducida de RAM y VRAM condiciona el diseño. La política de
VRAM Monogamy prevé un único modelo de IA residente en VRAM. Cualquier excepción
requiere mediciones y confirmación humana explícita; mientras no se apruebe,
no se permite residencia simultánea de dos modelos. Los embeddings tampoco
tienen una exención automática.

## 3. Núcleo, modelos y configuración

El núcleo previsto centralizará toda inferencia en `core/model_router.py` cuando
ese módulo exista. Deberá coordinar los roles de los modelos y la residencia en
VRAM, verificando la liberación de un modelo antes de cargar el siguiente.

Según el inventario proporcionado por el usuario, estos son los nombres exactos
disponibles en Ollama en esta máquina:

- `llava:7b`
- `qwen2.5-coder:7b`
- `qwen3:8b`
- `deepseek-r1:7b`
- `nomic-embed-text`
- `gemma2:9b`
- `llama3.1:latest`
- `phi4` — experimental; nunca será el modelo por defecto.

Este inventario no se ha vuelto a consultar en Fase 0. El tamaño mostrado por
`ollama list` es espacio en disco, no consumo de VRAM. La residencia y el consumo
real se medirán con `nvidia-smi` y `ollama ps` en fases posteriores; aquí no se
atribuyen cifras de VRAM a ningún modelo.

Las asignaciones de `user_settings.json` son defaults pendientes de validar.
Ninguna elección de brain, vision, coding, embeddings o reasoning_auditor es
definitiva. `qwen3:8b`, propuesto como brain/dispatcher, se someterá a validación
A/B en Fase 2 antes de fijar la decisión.

`config.py` está previsto en la raíz del proyecto, pero todavía NO se crea en
Fase 0: no existe ningún módulo real que necesite consumirlo. Se creará cuando
se implemente el primer módulo que lo requiera, previsto en Fase 1 con
`core/model_router.py`.

## 4. Notch flotante y estados

La GUI prevista usará PyQt6 y un notch flotante. `EonState` representará el
estado lógico del asistente, mientras que `NotchGeometryState` representará la
geometría visual. Ambos estarán desacoplados: cambiar la geometría no implicará
por sí mismo cambiar la actividad lógica ni autorizar acciones.

Las geometrías previstas son `PEEK` (vista mínima), `HOVER_PEEK` (vista de
anticipación al pasar el ratón) y `EXPANDED` (panel expandido). Se prevé ocultación
automática configurable; los valores iniciales del JSON todavía necesitan
validación con pantalla y ratón reales.

El notch mostrará colores según el estado lógico. La propuesta inicial es:

| Actividad lógica | Color propuesto |
| --- | --- |
| Reposo | Azul |
| Escucha | Cian |
| Procesamiento | Violeta |
| Ejecución | Verde |
| Confirmación humana requerida | Ámbar |
| Fallo o detención de seguridad | Rojo |

Esta paleta es diseño provisional: los tonos, el contraste y los estados lógicos
concretos se decidirán y validarán en la fase de GUI. El color irá acompañado de
una indicación textual. No hay colores, enumeraciones ni comportamiento de
interfaz implementados, y esta tabla no congela un contrato de runtime.

## 5. Personaje animado

Un character widget acompañará al notch mediante un personaje animado, con
recursos previstos en `assets/char/`. Su expresión reflejará la actividad del
asistente y las transiciones visuales. No hay personaje, sprites ni animaciones
creados todavía.

## 6. Voice Engine y wake-word

Se prevén reconocimiento de voz (STT), síntesis de voz (TTS), activación mediante
wake-word, barge-in para interrumpir la respuesta hablada y detección acústica.
El idioma de partida será `es-ES`; el perfil de voz permanece pendiente de
selección. La integración TTS Piper y sus librerías se decidirán en su fase.

La validación exige micrófono y audio reales. Los fallos de periféricos deberán
quedar registrados y gestionados sin tumbar el proceso principal.

## 7. Visión y control de pantalla

El subsistema `vision_actuator` está previsto para capturar e interpretar la
pantalla y ejecutar acciones mediante teclado y ratón bajo las restricciones
de autonomía y seguridad. La captura, las acciones y su resultado deberán
verificarse con hardware real. En Fase 0 no se captura ni controla la pantalla.

## 8. Safety Suite y Kill-Switch

La suite de seguridad incluirá Kill-Switch, Secrets Vault, Secret Scanner y
Voice Fingerprint Lock. El Kill-Switch tendrá como objetivo detener acciones
automatizadas y permitir al usuario recuperar el control. Su mecanismo concreto
y sus garantías se definirán y probarán al implementar el subsistema.

Los cambios futuros dentro de `safety/` requieren confirmación explícita del
usuario en texto plano dos veces, según [AGENTS.md](../AGENTS.md).

## 9. Secrets Vault y Secret Scanner

Secrets Vault será el punto de gestión de claves API y otros secretos. No se
guardarán en texto plano ni se versionarán en Git. Secret Scanner deberá detectar
exposiciones antes de que los cambios o proyectos generados se incorporen.
Todavía no existen almacenamiento seguro, escáner ni integración con keyring.

## 10. Voice Fingerprint Lock

Se prevé una comprobación de identidad vocal para restringir operaciones que
requieran autorización. Su alcance, métricas, manejo de errores y librerías se
decidirán en la fase de seguridad; no hay autenticación vocal implementada ni
se presupone que una voz reconocida autorice cualquier acción.

## 11. Genesis Engine

Genesis Engine fusionará self-programming y creación autónoma de proyectos
nuevos. El objetivo es proponer, construir y verificar cambios y proyectos
mediante checkpoints humanos obligatorios antes de las acciones que requieran
aprobación. Los checkpoints y las pruebas se concretarán al implementar la fase.

Genesis Engine y Self-Programmer nunca harán auto-merge a `main`, aunque todas las
pruebas automáticas pasen. La creación de repositorios en GitHub, el merge a
`main` y el push directo a `main` requieren confirmación humana explícita.

## 12. Thermal Guardian

Se prevé observar carga y estado térmico para adaptar o detener trabajo cuando
el equipo lo necesite. Umbrales, sensores y comportamiento ante lecturas fallidas
se fijarán mediante mediciones reales. No existe protección térmica de EON.

## 13. Mirror Test

Mirror Test se plantea como una revisión de la coherencia entre lo que EON afirma,
la evidencia disponible y los resultados de sus acciones. Sus criterios y pruebas
se definirán en su fase. No se atribuye conciencia ni capacidad de autoevaluación
verificada al proyecto.

## 14. Memoria cruzada entre proyectos

Cross-project Memory permitirá recuperar contexto útil entre proyectos con
separación de su procedencia y control sobre qué información se conserva o
reutiliza. El almacenamiento, los permisos y las reglas de retención se
concretarán al implementarlo. No hay bases de datos ni memoria persistente.

`nomic-embed-text` es un candidato para embeddings, sujeto a medición de residencia
real y a la misma política de VRAM que los demás modelos.

## 15. Confidence-Gated Autonomy

La autonomía prevista dependerá de la confianza respaldada por evidencia y del
alcance autorizado por el usuario. Ante incertidumbre o una acción que requiera
aprobación, EON deberá pasar por un checkpoint humano. Los umbrales y reglas no
están definidos ni implementados; la confianza no sustituye permisos obligatorios.

## 16. Multi-proveedor y Cost Guardian

Ollama local será el proveedor por defecto. Se prevé soporte opcional de pago
para OpenAI, Anthropic, Gemini, DeepSeek y OpenRouter, con selección explícita y
gestión de secretos a través de Secrets Vault.

Cost Guardian permitirá configurar límites de gasto y actuar al alcanzarlos.
Los defaults pendientes de validar son 2,0 USD diarios, 20,0 USD mensuales y
`fallback_to_local`. No existe contabilidad de consumo, bloqueo de gasto ni
fallback implementado; el JSON no activa ninguna conexión externa.

## 17. Estructura y situación actual

Las carpetas previstas son `core/`, `gui/`, `safety/`, `assets/char/`,
`assets/sounds/`, `tests/` y `.runtime/`. Solo contienen `.gitkeep` en Fase 0.

Los contratos se incorporarán en [CONTRACTS.md](CONTRACTS.md) conforme existan
módulos reales. [STATUS.md](STATUS.md) describe la implementación efectiva:
ningún subsistema y ningún archivo `.py`. Los `.bat` iniciales son comprobaciones
previas y un aviso de construcción, no instaladores ni lanzadores funcionales.
