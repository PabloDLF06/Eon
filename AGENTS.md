# EON — Reglas permanentes

Estas reglas se aplican a todas las fases futuras del proyecto.

- Nunca cargar dos modelos de IA en VRAM simultáneamente; toda inferencia pasará
  por `core/model_router.py` cuando exista.
- Cero placeholders, cero "TODO", cero funciones incompletas en cualquier código
  Python futuro. Si algo no se puede completar, decirlo explícitamente; nunca
  ocultarlo en el código.
- Cada módulo nuevo en `core/` o `gui/` debe tener su test correspondiente en `tests/`.
- Todo acceso a hardware (micrófono, GPU, teclado, ratón, pantalla) debe envolverse
  en `try/except` con logging; un fallo periférico nunca debe tumbar el proceso
  principal.
- Textos de cara al usuario en español. Nombres de variables, funciones y
  comentarios de código en inglés.
- Nunca modificar nada dentro de `safety/` sin confirmación explícita y repetida
  del usuario, en texto plano, dos veces.
- Nunca hacer merge a `main`, ni push directo a `main`, ni crear un repositorio
  nuevo en GitHub, sin confirmación explícita del usuario.
- Nunca hacer auto-merge a `main` desde Genesis Engine ni desde Self-Programmer,
  aunque todos los tests automáticos pasen.
- Las claves API y cualquier secreto nunca se guardan en texto plano ni se
  versionan en Git; deben pasar siempre por Secrets Vault.
- No afirmar que una funcionalidad existe o fue verificada sin comprobarlo
  realmente.
- Antes de escribir código nuevo en cualquier sesión futura, leer primero
  `docs/STATUS.md` y `docs/CONTRACTS.md`.
- Actualizar `docs/STATUS.md` al final de cada sesión de trabajo.
