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
