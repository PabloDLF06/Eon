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
