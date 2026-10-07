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
