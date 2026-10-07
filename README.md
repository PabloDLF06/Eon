# EON

EON está en fase de scaffolding inicial. La Fase 0 prepara documentación y
estructura de carpetas; todavía no hay subsistemas implementados ni archivos `.py`.

El contenido arquitectónico de [docs/SPEC.md](docs/SPEC.md) es diseño previsto,
no funcionalidad existente. El estado real está en [docs/STATUS.md](docs/STATUS.md),
las fases en [docs/PHASES.md](docs/PHASES.md) y las reglas permanentes en
[AGENTS.md](AGENTS.md).

El desarrollo es exclusivamente local en `C:\Dev\Eon`, con Codex Desktop en modo
«Este ordenador». No hay flujo alternativo de Codex Cloud/App Web.

`user_settings.json` contiene defaults pendientes de validar. `requirements.txt`
es una propuesta de dependencias para fases posteriores; no se instalan ahora.
`install.bat` solo comprueba requisitos y `start.bat` solo avisa de que EON está
en construcción. Ninguno inicia funcionalidades de EON.

## Filosofía

Cuando el razonamiento disponible es limitado —ya sea por el tamaño del modelo o por la ventana de contexto—, la disciplina estructural es lo que salva el resultado.
