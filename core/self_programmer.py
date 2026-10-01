"""Motor de auto-programación con aislamiento en Git (spec 4.5).

Cuando Pablo dice «Eon, añade una función para avisarme cuando reciba un
correo», EON escribe código nuevo. Eso es aterrador si se hace sobre el árbol de
trabajo, así que la regla es: **ninguna modificación sale de una sandbox
verificada**.

Flujo:

1. ``git status`` limpio (si no, se aborta: nada de mezclar cambios del usuario
   con los del modelo).
2. Rama nueva ``feature/self-upgrade-<marca-de-tiempo>``.
3. ``qwen2.5-coder:7b`` devuelve un *manifiesto* JSON con los archivos. Se
   validan rutas (fuera de ``safety/``, sin ``..``, sin absolutas, extensiones
   y tamaños máximos) antes de tocar disco.
4. Se añade un test y se corre ``pytest tests/`` en un subproceso aislado con
   tiempo límite.
5. Pruebas en verde -> merge en la rama base + commit detallado + *hot reload*.
   Pruebas en rojo -> ``git reset --hard`` + volver a la rama base + aviso verbal.

La verificación de integridad del kill switch (``safety/killswitch.py``) vive en
el paso 3 y no se puede desactivar desde aquí: si este módulo pudiera reescribir
ese archivo, el hash cambiaría y EON no arrancaría. Es circular a propósito.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from safety.killswitch import EonPaused, assert_writable_path

log = logging.getLogger("eon.self_programmer")

#: Archivos que el modelo puede crear/modificar, por extensión.
ALLOWED_SUFFIXES: tuple[str, ...] = (".py", ".md", ".json", ".txt", ".html", ".css", ".js", ".toml", ".cfg")
#: Directorios donde el modelo puede escribir dentro del repo.
ALLOWED_ROOTS: tuple[str, ...] = ("core", "gui", "assets", "tests", "docs", "workspace")

CODEGEN_SYSTEM = """Eres el motor de auto-mejora de EON, un asistente de escritorio en Python 3 + PyQt6.
Escribes código completo, ejecutable y sin marcadores de pendiente.

Devuelve SÓLO un objeto JSON, sin prosa ni cercas de código:

{"summary": "qué se ha hecho en una frase",
 "branch_hint": "nombre-corto-kebab",
 "files": [{"path": "core/ruta.py", "content": "archivo completo"},
           {"path": "tests/test_ruta.py", "content": "tests pytest reales"}],
 "notes": "avisos para el usuario, en español"}

Reglas duras:
- NUNCA toques safety/, config.py, install.bat, start.bat ni requirements*.txt.
- Cada "content" es el archivo ENTERO (no diffs, no fragments, no " resto igual").
- Añade siempre al menos un test en tests/ que falle si la función no funciona.
- Sin dependencias nuevas. Sin red. Sin subprocess sobre el disco del usuario.
- Docstrings y comentarios en español claro.
- Máximo 6 archivos.
"""

_JSON_OBJECT = re.compile(r"\{[\s\S]*\}")


# --------------------------------------------------------------------------- #
# Tipos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PatchFile:
    """Un archivo del manifiesto, con su contenido ya validado."""

    path: str
    content: str

    @property
    def is_new(self) -> bool:
        return False  # se resuelve contra el disco en el momento de escribir


@dataclass(frozen=True)
class TestReport:
    """Resultado de la suite automatizada."""

    passed: bool
    returncode: int
    summary: str
    output: str = ""
    duration_s: float = 0.0
    command: tuple[str, ...] = ()

    @property
    def tests_ok(self) -> bool:
        return self.passed and self.returncode == 0


@dataclass
class UpgradeResult:
    """Resultado extremo a extremo del intento de mejora."""

    ok: bool
    stage: str = ""
    branch: str = ""
    commit: str = ""
    summary: str = ""
    files: tuple[str, ...] = ()
    tests: TestReport | None = None
    detail: str = ""
    rolled_back: bool = False
    hot_reloaded: bool = False
    spoken: str = ""

    def message(self) -> str:
        """Frase para el TTS: lo que Pablo tiene que oír, no un dict."""
        if self.ok:
            return f"Listo, {self.summary or 'funcionalidad añadida'}. Reinicio EON para aplicarla."
        if self.rolled_back:
            return "No pude añadir la funcionalidad porque fallaron las pruebas de seguridad internas."
        return self.detail or "No pude completar la auto-mejora."


# --------------------------------------------------------------------------- #
# Git
# --------------------------------------------------------------------------- #


class GitSandbox:
    """Envoltorio de ``git`` con tiempo límite y sin shell.

    Todo comando se ejecuta con ``cwd=root`` y ``--no-pager``; el ``runner`` es
    inyectable para que los tests no toquen el repositorio real.
    """

    def __init__(self, root: Path | None = None, logger: logging.Logger | None = None, runner: Callable[..., subprocess.CompletedProcess] | None = None, timeout: float | None = None) -> None:
        try:
            import config

            self.root = Path(root or config.ROOT_DIR)
            self.timeout = float(timeout or config.GIT_TIMEOUT_S)
        except Exception:  # pragma: no cover
            self.root = Path(root or Path.cwd())
            self.timeout = float(timeout or 60.0)
        self.log = logger or log
        self._runner = runner
        self.commands: list[list[str]] = []

    # ------------------------------------------------------------------ crudo --
    def run(self, args: Sequence[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        command = ["git", "--no-pager", *args]
        self.commands.append(command)
        if self._runner is not None:
            return self._runner(command, self.root, timeout or self.timeout)
        try:
            return subprocess.run(
                command,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(command, returncode=127, stdout="", stderr=str(exc))

    def ok(self, args: Sequence[str], timeout: float | None = None) -> tuple[bool, str]:
        result = self.run(args, timeout)
        output = (result.stdout or result.stderr or "").strip()
        return result.returncode == 0, output

    # ------------------------------------------------------------------ API --
    def available(self) -> bool:
        inside, _ = self.ok(["rev-parse", "--is-inside-work-tree"])
        return inside

    def current_branch(self) -> str:
        _ok, name = self.ok(["rev-parse", "--abbrev-ref", "HEAD"])
        return name or "HEAD"

    def is_clean(self) -> bool:
        clean, output = self.ok(["status", "--porcelain"])
        return clean and not output.strip()

    def has_remotes(self) -> bool:
        ok, output = self.ok(["remote"])
        return ok and bool(output.strip())

    def create_branch(self, name: str, base: str | None = None) -> tuple[bool, str]:
        args = ["checkout", "-b", name] + ([base] if base else [])
        return self.ok(args)

    def checkout(self, name: str) -> tuple[bool, str]:
        return self.ok(["checkout", name])

    def add(self, paths: Sequence[str]) -> tuple[bool, str]:
        return self.ok(["add", "-A", "--", *paths]) if paths else (True, "")

    def commit(self, message: str) -> tuple[bool, str]:
        result = self.run(["commit", "-m", message])
        ok = result.returncode == 0
        sha = ""
        if ok:
            _again, sha = self.ok(["rev-parse", "--short", "HEAD"])
        return ok, sha or (result.stdout or result.stderr).strip()

    def merge_fast_forward(self, branch: str, into: str) -> tuple[bool, str]:
        """Fusiona ``branch`` en ``into`` sólo si es lineal (nada de merges raros)."""
        switched, output = self.ok(["checkout", into])
        if not switched:
            return False, output
        return self.ok(["merge", "--ff-only", branch])

    def reset_hard(self, ref: str = "HEAD") -> tuple[bool, str]:
        return self.ok(["reset", "--hard", ref])

    def clean_untracked(self) -> tuple[bool, str]:
        """Elimina los archivos nuevos que dejó el modelo (incluye los tests)."""
        return self.ok(["clean", "-fd", "--", *[f"{root}/" for root in ALLOWED_ROOTS]])

    def diff_stat(self) -> str:
        _ok, output = self.ok(["diff", "--stat", "HEAD~1", "HEAD"])
        return output


def sanitize_branch_hint(hint: str, fallback: str = "mejora") -> str:
    """``"Avisos de correo!"`` -> ``"avisos-de-correo"`` (rama git válida)."""
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", (hint or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    return text[:40] or fallback


def branch_name(prefix: str | None = None, when: datetime | None = None, hint: str = "") -> str:
    """Nombre de rama ``feature/self-upgrade-<YYYYmmdd-HHMMSS>[-hint]``."""
    try:
        import config

        prefix = prefix or config.SELF_UPGRADE_BRANCH_PREFIX
    except Exception:  # pragma: no cover
        prefix = prefix or "feature/self-upgrade-"
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{sanitize_branch_hint(hint)}" if hint else ""
    return f"{prefix}{stamp}{suffix}"


# --------------------------------------------------------------------------- #
# Manifiesto del modelo
# --------------------------------------------------------------------------- #


def parse_manifest(text: str) -> tuple[list[PatchFile], str, str]:
    """Analiza la salida del coder -> ``(archivos, resumen, error)``.

    Se aceptan variantes habituales de los modelos pequeños (campos en inglés o
    español, JSON dentro de texto, comas sobrantes). Un manifiesto inválido es un
    resultado esperado y se reporta, no se lanza.
    """
    payload, error = _load_json_object(text or "")
    if error:
        return [], "", error
    files: list[PatchFile] = []
    raw_files = payload.get("files") or payload.get("archivos") or payload.get("patches") or []
    if isinstance(raw_files, dict):
        raw_files = [{"path": key, "content": value} for key, value in raw_files.items()]
    for item in raw_files if isinstance(raw_files, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("ruta") or item.get("file") or "").strip().replace("\\", "/")
        content = item.get("content")
        if content is None:
            content = item.get("contenido") or item.get("code") or ""
        if not path or not isinstance(content, str) or not content.strip():
            continue
        files.append(PatchFile(path=path, content=content.replace("\r\n", "\n")))
    summary = str(payload.get("summary") or payload.get("resumen") or "").strip()
    hint = str(payload.get("branch_hint") or payload.get("hint") or "").strip()
    if hint:
        summary = f"{summary} [{hint}]" if summary else hint
    if not files:
        return [], summary, "el modelo no devolvió ningún archivo"
    return files, summary, ""


def _load_json_object(text: str) -> tuple[dict[str, Any], str]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    for candidate in (cleaned, *(_braced(cleaned)), re.sub(r",\s*([}\]])", r"\1", cleaned)):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data, ""
    return {}, "respuesta del coder sin JSON válido"


def _braced(text: str) -> list[str]:
    match = _JSON_OBJECT.search(text)
    return [match.group(0)] if match else []


def validate_files(files: Sequence[PatchFile], root: Path) -> tuple[list[PatchFile], list[str]]:
    """Filtra el manifiesto por las reglas de seguridad del repo.

    Devuelve ``(aceptados, rechazos)``. Cada rechazo explica el motivo en
    español, porque el registro es lo que lee Pablo cuando algo no pasó.
    """
    try:
        import config

        max_files = int(getattr(config, "APP_BUILDER_CONFIG", {}).get("max_files", 40))
        max_bytes = int(getattr(config, "APP_BUILDER_CONFIG", {}).get("max_file_bytes", 120_000))
    except Exception:  # pragma: no cover
        max_files, max_bytes = 40, 120_000
    accepted: list[PatchFile] = []
    rejected: list[str] = []
    for item in files[: max_files + 10]:
        reason = _reject_reason(item, root, max_bytes)
        if reason:
            rejected.append(f"{item.path}: {reason}")
            continue
        accepted.append(item)
    if len(accepted) > max_files:
        rejected.append(f"manifiesto recortado a {max_files} archivos")
        accepted = accepted[:max_files]
    return accepted, rejected


def _reject_reason(item: PatchFile, root: Path, max_bytes: int) -> str:
    path = Path(item.path)
    if path.is_absolute() or ".." in path.parts:
        return "ruta fuera del repositorio"
    top = path.parts[0].lower() if path.parts else ""
    if top in {"safety", ".git", "assets/integrity"}:
        return "directorio protegido"
    if path.name.lower() in {"config.py", "killswitch.py", "main.py", "install.bat", "start.bat"}:
        return "archivo protegido"
    if top not in ALLOWED_ROOTS:
        return f"directorio no permitido (usa {'/'.join(ALLOWED_ROOTS)})"
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return "extensión no permitida"
    if len(item.content.encode("utf-8")) > max_bytes:
        return "archivo demasiado grande"
    if not item.content.endswith("\n"):
        item = PatchFile(path=item.path, content=item.content + "\n")
    try:
        assert_writable_path(root / path, root=root)
    except EonPaused as exc:
        return str(exc)
    if path.suffix.lower() == ".py":
        try:
            compile(item.content, str(path), "exec")
        except SyntaxError as exc:
            return f"error de sintaxis en la línea {exc.lineno}"
    return ""


def test_command(python_exe: str | None = None) -> tuple[str, ...]:
    """Comando de la suite, siempre con el intérprete actual (no depende del PATH)."""
    return (python_exe or sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-p", "no:cacheprovider")


# --------------------------------------------------------------------------- #
# Programador
# --------------------------------------------------------------------------- #


@dataclass
class SelfProgrammer:
    """Orquesta la auto-mejora de principio a fin.

    ``notify`` permite decir algo en voz alta en cada hito sin acoplar este
    módulo al motor de voz (``main.py`` le pasa ``voice.say``).
    """

    router: Any | None = None
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    git: GitSandbox | None = None
    notify: Callable[[str], None] | None = None
    on_state: Callable[[str, dict], None] | None = None
    logger: logging.Logger | None = None
    test_timeout: float | None = None
    allow_push: bool | None = None
    hot_reload: bool | None = None

    def __post_init__(self) -> None:
        self.log = self.logger or log
        self.root = Path(self.root)
        self.git = self.git or GitSandbox(self.root, logger=self.log)
        self._lock = threading.Lock()
        self._busy = False
        try:
            import config

            self.test_timeout = float(self.test_timeout or config.PYTEST_TIMEOUT_S)
            self.allow_push = bool(config.AUTO_PUSH) if self.allow_push is None else bool(self.allow_push)
            self.hot_reload = bool(config.HOT_RELOAD_ON_MERGE) if self.hot_reload is None else bool(self.hot_reload)
        except Exception:  # pragma: no cover
            self.test_timeout = float(self.test_timeout or 240.0)
            self.allow_push = False if self.allow_push is None else bool(self.allow_push)
            self.hot_reload = True if self.hot_reload is None else bool(self.hot_reload)

    @property
    def busy(self) -> bool:
        return self._busy

    # ------------------------------------------------------------------ API --
    def implement(self, instruction: str) -> UpgradeResult:
        """Pipeline completo. Nunca lanza: siempre devuelve un resultado narrable."""
        instruction = (instruction or "").strip()
        if not instruction:
            return UpgradeResult(ok=False, stage="entrada", detail="No he recibido la orden que aplicar.")
        with self._lock:
            if self._busy:
                return UpgradeResult(ok=False, stage="ocupado", detail="Ya estoy compilando otra mejora.")
            self._busy = True
        try:
            return self._implement_locked(instruction)
        except Exception as exc:  # la última barrera: ni un bug debe tumbar EON
            self.log.exception("auto-programación abortada")
            return UpgradeResult(ok=False, stage="excepción", detail=f"Fallo interno: {exc}", rolled_back=True)
        finally:
            with self._lock:
                self._busy = False

    # ------------------------------------------------------------- pipeline --
    def _implement_locked(self, instruction: str) -> UpgradeResult:
        if not self.git.available():
            return UpgradeResult(ok=False, stage="git", detail="Esto no es un repositorio de Git; no puedo auto-modificarme de forma segura.")
        if not self.git.is_clean():
            return UpgradeResult(
                ok=False,
                stage="git",
                detail="Hay cambios sin guardar en el proyecto. Guárdalos o descártalos y vuelve a pedírmelo.",
            )
        base = self.git.current_branch() or "main"
        self._emit("generating", {"instruction": instruction[:120]})
        files, summary, error = self._generate(instruction)
        if error:
            return UpgradeResult(ok=False, stage="modelo", detail=error, summary=summary)
        accepted, rejected = validate_files(files, self.root)
        if not accepted:
            return UpgradeResult(ok=False, stage="validación", detail="; ".join(rejected) or "manifiesto vacío", summary=summary)
        branch = branch_name(hint=summary or instruction)
        created, output = self.git.create_branch(branch, base)
        if not created:
            return UpgradeResult(ok=False, stage="rama", detail=output or "no se pudo crear la rama", summary=summary)
        written = self._write(accepted)
        self._emit("writing", {"files": written, "rejected": rejected})
        tests = self._run_tests()
        if not tests.tests_ok:
            self._rollback(base, branch)
            return UpgradeResult(
                ok=False,
                stage="pruebas",
                branch=branch,
                summary=summary,
                files=tuple(written),
                tests=tests,
                rolled_back=True,
                detail=f"Fallaron las pruebas: {tests.summary}",
            )
        commit_ok, commit = self.git.commit(self._commit_message(summary, instruction, rejected))
        merged, merge_output = self.git.merge_fast_forward(branch, base)
        pushed = ""
        if merged and self.allow_push and self.git.has_remotes():
            pushed_ok, pushed_out = self.git.ok(["push", "origin", base])
            pushed = "remoto actualizado" if pushed_ok else f"sin push: {pushed_out[:80]}"
        hot = False
        if merged and self.hot_reload:
            hot = self.schedule_restart()
        self._emit("done", {"branch": branch, "commit": commit, "merged": merged})
        return UpgradeResult(
            ok=bool(merged and commit_ok),
            stage="completado",
            branch=branch,
            commit=commit,
            summary=summary,
            files=tuple(written),
            tests=tests,
            detail=" · ".join(part for part in (merge_output if not merged else "", pushed) if part),
            hot_reloaded=hot,
        )

    def _generate(self, instruction: str) -> tuple[list[PatchFile], str, str]:
        if self.router is None:
            return [], "", "El motor de modelos (Ollama) no está disponible."
        prompt = (
            "Proyecto EON (asistente de escritorio en Python 3.11 + PyQt6, sin red, sin telemetría).\n"
            "Árbol: core/ (modelos, voz, visión, auto-mejora), gui/ (notch, personaje, glow), "
            "safety/ (intocable), tests/ (pytest).\n"
            "Dependencias ya instaladas: PyQt6, numpy, pillow, sounddevice, mss, pyautogui, "
            "faster-whisper, piper-tts, openwakeword, webrtcvad-wheels, psutil.\n"
            "Cada módulo usa logging y degrada con elegancia si falta un periférico.\n\n"
            f"ORDEN DE PABLO: {instruction}\n\n"
            "Recuerda: el archivo nuevo va en core/ o gui/, su test en tests/, y nada de placeholders."
        )
        try:
            result = self.router.generate(prompt, role="coder", system=CODEGEN_SYSTEM, options={"temperature": 0.15, "num_predict": 2600, "num_ctx": 8192})
        except Exception as exc:
            return [], "", f"El modelo de código no respondió: {exc}"
        files, summary, error = parse_manifest(result.text)
        return files, summary, error

    def _write(self, files: Sequence[PatchFile]) -> list[str]:
        written: list[str] = []
        for item in files:
            target = assert_writable_path(self.root / item.path, root=self.root)
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item.content, encoding="utf-8")
            written.append(item.path)
        return written

    def _run_tests(self) -> TestReport:
        command = test_command()
        started = time.perf_counter()
        try:
            result = subprocess.run(
                list(command),
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.test_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return TestReport(False, 124, f"tiempo límite de {self.test_timeout:.0f} s superado", "", time.perf_counter() - started, command)
        except (OSError, ValueError) as exc:
            return TestReport(False, 127, f"no se pudo lanzar pytest: {exc}", "", time.perf_counter() - started, command)
        tail = _test_tail(result.stdout or "", result.stderr or "")
        passed = result.returncode == 0
        return TestReport(passed, int(result.returncode), tail or ("todo en verde" if passed else "salida vacía"), (result.stdout or "")[-6000:], time.perf_counter() - started, command)

    def _rollback(self, base: str, branch: str) -> None:
        """Deshace todo: el repo vuelve a estar como antes de pedírmelo."""
        self._emit("rolling_back", {"branch": branch})
        self.git.reset_hard("HEAD")
        self.git.checkout(base)
        self.git.reset_hard(base)
        self.git.clean_untracked()
        # la rama de trabajo sobrante se borra para no acumular basura
        self.git.ok(["branch", "-D", branch])
        self.log.warning("auto-mejora revertida; se descarta la rama %s", branch)

    def _commit_message(self, summary: str, instruction: str, rejected: Sequence[str]) -> str:
        lines = [f"auto: {summary or 'mejora solicitada por voz'}", "", f"Solicitado por Pablo: {instruction.strip()[:400]}"]
        if rejected:
            lines += ["", "Archivos rechazados por las reglas de seguridad:", *[f"- {item}" for item in rejected[:6]]]
        lines += ["", f"Generado por {getattr(self.router, 'model_for', lambda _: 'qwen2.5-coder')('coder')} tras superar pytest."]
        return "\n".join(lines)

    # -------------------------------------------------------------- reinicio --
    def schedule_restart(self, delay_s: float = 1.2) -> bool:
        """Re-invoca ``sys.executable main.py`` y deja que el proceso actual muera.

        Es un *hot reload* a la vieja usanza: para módulos de C ySingletons de
        Qt, relanzar el intérprete es más fiable que vaciar ``sys.modules``. El
        retraso da tiempo a que el TTS termine la frase de confirmación.
        """
        root = self.root
        entry = root / "main.py"
        if not entry.exists():
            self.log.warning("no encuentro %s para reiniciar", entry)
            return False
        self._notify("Aplico los cambios y reinicio EON, señor.")
        command = [sys.executable, str(entry)]

        def _spawn() -> None:
            time.sleep(max(0.2, delay_s))
            try:
                flags = 0
                if sys.platform == "win32":
                    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                subprocess.Popen(
                    command,
                    cwd=str(root),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            except (OSError, ValueError) as exc:
                self.log.error("no se pudo relanzar EON: %s (cambia a start.bat manualmente)", exc)
                return
            self.log.warning("reiniciando EON tras la auto-mejora")
            for handler in logging.getLogger("eon").handlers:
                try:
                    handler.flush()
                except Exception:
                    pass
            os._exit(0)

        threading.Thread(target=_spawn, name="eon-restart", daemon=True).start()
        return True

    # ------------------------------------------------------------------ varios --
    def status(self) -> dict[str, Any]:
        return {
            "git": self.git.available(),
            "branch": self.git.current_branch(),
            "clean": self.git.is_clean(),
            "busy": self._busy,
            "push": self.allow_push,
        }

    def _notify(self, text: str) -> None:
        if self.notify is not None:
            try:
                self.notify(text)
            except Exception:  # hablar nunca rompe la compilación
                pass

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        self.log.info("auto-mejora [%s] %s", name, payload)
        if self.on_state is None:
            return
        try:
            self.on_state(name, payload)
        except Exception:
            pass


def _test_tail(stdout: str, stderr: str) -> str:
    """La línea resumen de pytest (``5 passed in 1.2s``) o el error corto."""
    blob = "\n".join(part for part in (stdout, stderr) if part)
    lines = [line.strip() for line in blob.splitlines() if line.strip()]
    for line in reversed(lines):
        if re.search(r"\b\d+ (passed|failed|error)", line):
            return line[:200]
    for line in reversed(lines):
        if "FAILED" in line or "ERROR" in line:
            return line[:200]
    return (lines[-1] if lines else "")[:200]

