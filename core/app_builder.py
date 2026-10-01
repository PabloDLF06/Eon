"""Fábrica autónoma de aplicaciones: de la idea hablada al programa en marcha (spec 4.6).

Cuando Pablo dice «Eon, créame una aplicación de notas minimalista con fondo
oscuro», EON:

1. crea ``workspace/projects/<nombre-slug>/``;
2. pide a ``qwen2.5-coder:7b`` un manifiesto JSON con los archivos;
3. valida cada ruta (sin ``..``, sin absolutas, extensión y tamaño permitidos)
   antes de escribir;
4. verifica: compila los ``.py``, revisa que el HTML tenga ``<html>``/``<body>``
   y que el JS no tenga corchetes desbalanceados;
5. la abre: web -> navegador (Comet si existe, si no, el predeterminado, en modo
   ``--app``); python -> ``pythonw`` en un proceso suelto.

Si el modelo no está disponible, se usan plantillas propias completas y el
resultado sigue siendo funcional: el usuario no obtiene un error, obtiene una
app. La fábrica mejora cuando el coder está despierto, pero nunca depende de
él para entregar algo utilizable.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger("eon.app_builder")

BRAND = "EON"

CODEGEN_SYSTEM = """Eres la fábrica de aplicaciones de EON. Construyes apps completas y funcionales,
sin dependencias de red ni CDNs: todo el CSS y JS va en archivos locales.

Devuelve SÓLO JSON, sin prosa ni cercas de código:

{"name": "notas-minimalistas", "stack": "web" | "python", "summary": "qué hace",
 "files": [{"path": "index.html", "content": "..."}, {"path": "style.css", "content": "..."}],
 "entry": "index.html", "run": "python app.py"}

Reglas:
- 3 a 8 archivos. Rutas relativas, sin "..", sin nombres absolutos.
- Idioma de la interfaz: español. Diseño: oscuro, esquinas suaves, acento #00e5ff.
- Web: index.html + style.css + app.js; el estado se guarda en localStorage.
- Python: un único app.py con tkinter (sin dependencias externas) y ventana visible.
- Sin claves API, sin fetch a dominios externos, sin eval.
- Nada de marcadores de pendiente: el código se ejecuta tal cual.
"""

_WEB_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{TITLE}}</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <main class="shell">
    <header class="bar">
      <h1>{{TITLE}}</h1>
      <div class="actions">
        <button id="new" class="primary">Nueva</button>
        <input id="search" type="search" placeholder="Buscar…" aria-label="Buscar" />
      </div>
    </header>
    <section class="compose">
      <textarea id="draft" rows="4" placeholder="Escribe aquí y pulsa Guardar (Ctrl+Enter)"></textarea>
      <div class="row">
        <span id="counter" class="dim">0 caracteres</span>
        <div>
          <button id="save">Guardar</button>
          <button id="clear" class="danger">Vaciar todo</button>
        </div>
      </div>
    </section>
    <ul id="list" class="notes" aria-live="polite"></ul>
    <p id="empty" class="dim hidden">Todavía no hay nada guardado.</p>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""

_WEB_CSS = """:root {
  --bg: #0d0f14;
  --panel: #151923;
  --line: #232a38;
  --text: #e9eef7;
  --dim: #8b95a8;
  --accent: #00e5ff;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: radial-gradient(1200px 600px at 50% -10%, #16202b 0%, var(--bg) 60%);
  color: var(--text);
  font: 15px/1.5 "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
}

.shell { max-width: 820px; margin: 0 auto; padding: 28px 20px 60px; }

.bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.bar h1 { font-size: 19px; margin: 0; letter-spacing: .3px; }
.actions { display: flex; gap: 10px; align-items: center; }

button, input, textarea {
  font: inherit; color: inherit; background: var(--panel);
  border: 1px solid var(--line); border-radius: 12px; padding: 9px 13px;
}
button { cursor: pointer; transition: .15s border-color, .15s transform; }
button:hover { border-color: var(--accent); transform: translateY(-1px); }
button.primary { border-color: color-mix(in srgb, var(--accent) 60%, var(--line)); }
button.danger { color: #ff8ea3; }
input[type="search"] { min-width: 180px; }

.compose { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 14px; }
.compose textarea { width: 100%; resize: vertical; background: #101420; border-color: transparent; }
.row { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; gap: 12px; }
.dim { color: var(--dim); font-size: 13px; }
.hidden { display: none; }

.notes { list-style: none; padding: 0; margin: 18px 0 0; display: grid; gap: 10px; }
.notes li {
  background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 14px; padding: 12px 14px; display: flex; gap: 12px; align-items: flex-start;
}
.notes p { margin: 0; white-space: pre-wrap; word-break: break-word; flex: 1; }
.notes time { color: var(--dim); font-size: 12px; white-space: nowrap; }
"""

_WEB_JS = """/** EON · notas locales. Todo el estado vive en localStorage: cero red, cero cuentas. */
(() => {
  const KEY = "eon.notes.v1";
  const list = document.getElementById("list");
  const empty = document.getElementById("empty");
  const draft = document.getElementById("draft");
  const counter = document.getElementById("counter");
  const search = document.getElementById("search");

  let notes = load();

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      console.warn("estado corrupto, se empieza de cero", err);
      return [];
    }
  }

  function save() {
    localStorage.setItem(KEY, JSON.stringify(notes));
  }

  function render() {
    const query = search.value.trim().toLowerCase();
    const visible = query ? notes.filter((note) => note.text.toLowerCase().includes(query)) : notes;
    list.innerHTML = "";
    visible.forEach((note) => {
      const item = document.createElement("li");
      const text = document.createElement("p");
      text.textContent = note.text;
      const when = document.createElement("time");
      when.textContent = new Date(note.at).toLocaleString("es-ES");
      const remove = document.createElement("button");
      remove.textContent = "Borrar";
      remove.addEventListener("click", () => {
        notes = notes.filter((candidate) => candidate.id !== note.id);
        save();
        render();
      });
      item.append(text, when, remove);
      list.append(item);
    });
    empty.classList.toggle("hidden", visible.length > 0);
  }

  function commit() {
    const text = draft.value.trim();
    if (!text) return;
    notes.unshift({ id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()), text, at: Date.now() });
    draft.value = "";
    counter.textContent = "0 caracteres";
    save();
    render();
  }

  document.getElementById("save").addEventListener("click", commit);
  document.getElementById("new").addEventListener("click", () => draft.focus());
  document.getElementById("clear").addEventListener("click", () => {
    if (confirm("¿Borrar todas las notas?")) {
      notes = [];
      save();
      render();
    }
  });
  search.addEventListener("input", render);
  draft.addEventListener("input", () => {
    counter.textContent = `${draft.value.length} caracteres`;
  });
  draft.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      commit();
    }
  });

  render();
})();
"""

_PYTHON_APP = '''"""{{TITLE}} — generada por EON.

Una sola ventana de tkinter, sin dependencias externas: se puede ejecutar con
``python app.py`` en cualquier Windows con el intérprete estándar.
"""

from __future__ import annotations

import json
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

ACCENT = "#00e5ff"
BG = "#0d0f14"
PANEL = "#151923"
LINE = "#232a38"
TEXT = "#e9eef7"
DIM = "#8b95a8"

DATA_FILE = Path.home() / ".eon" / "{{SLUG}}.json"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("{{TITLE}}")
        self.configure(bg=BG)
        self.geometry("760x520")
        self.minsize(520, 360)
        self.items: list[dict] = []
        self._build()
        self._load()
        self._render()

    # ------------------------------------------------------------ construcción
    def _build(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(header, text="{{TITLE}}", bg=BG, fg=TEXT, font=("Segoe UI", 15, "bold")).pack(side="left")
        tk.Button(
            header, text="Nueva", command=self._new, bg=PANEL, fg=ACCENT,
            activebackground=LINE, relief="flat", bd=0, highlightthickness=1, highlightbackground=LINE,
        ).pack(side="right", padx=(6, 0))

        self.draft = tk.Text(self, height=4, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", wrap="word",
                             highlightthickness=1, highlightbackground=LINE)
        self.draft.pack(fill="x", padx=16)
        self.draft.bind("<Control-Return>", lambda _event: self._save())

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(10, 16))
        self.listbox = tk.Listbox(
            body, bg=PANEL, fg=TEXT, selectbackground=ACCENT, selectforeground="#04222b",
            relief="flat", highlightthickness=1, highlightbackground=LINE, activestyle="none",
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        side = tk.Frame(body, bg=BG, width=190)
        side.pack(side="right", fill="y", padx=(10, 0))
        for label, command in (("Guardar", self._save), ("Borrar", self._delete), ("Exportar JSON", self._export)):
            tk.Button(side, text=label, command=command, bg=PANEL, fg=TEXT, relief="flat", bd=0,
                      activebackground=LINE, highlightthickness=1, highlightbackground=LINE).pack(fill="x", pady=(0, 6))
        self.status = tk.Label(side, text="", bg=BG, fg=DIM, justify="left", anchor="w")
        self.status.pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------- utilidades
    def _load(self) -> None:
        try:
            if DATA_FILE.exists():
                payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                self.items = payload if isinstance(payload, list) else []
        except (OSError, ValueError) as exc:
            print(f"No se pudo leer el estado previo: {exc}")
            self.items = []

    def _persist(self) -> None:
        try:
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            DATA_FILE.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self._say(f"No se pudo guardar: {exc}")

    def _render(self) -> None:
        self.listbox.delete(0, "end")
        for item in self.items:
            stamp = datetime.fromtimestamp(item.get("at", 0)).strftime("%d/%m %H:%M")
            first = (item.get("text") or "").splitlines() or [""]
            self.listbox.insert("end", f"{stamp}  {first[0][:60]}")
        self._say(f"{len(self.items)} elemento(s)")

    def _say(self, text: str) -> None:
        self.status.config(text=text)

    # --------------------------------------------------------------- acciones
    def _save(self) -> None:
        text = self.draft.get("1.0", "end").strip()
        if not text:
            return
        self.items.insert(0, {"text": text, "at": time.time()})
        self.draft.delete("1.0", "end")
        self._persist()
        self._render()

    def _delete(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        del self.items[index]
        self._persist()
        self._render()

    def _new(self) -> None:
        self.draft.focus_set()

    def _export(self) -> None:
        path = Path.home() / "Desktop" / "{{SLUG}}.json"
        try:
            path.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")
            self._say(f"Exportado a {path.name}")
        except OSError as exc:
            self._say(f"Error al exportar: {exc}")


if __name__ == "__main__":
    App().mainloop()
'''


# --------------------------------------------------------------------------- #
# Utilidades puras
# --------------------------------------------------------------------------- #


def slugify(text: str, fallback: str = "app") -> str:
    """``"¡Notas Minimalistas!"`` -> ``"notas-minimalistas"`` (ascii, sin espacios)."""
    normalized = unicodedata.normalize("NFKD", (text or "").strip().lower())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:38].strip("-")
    return cleaned or fallback


def slug_from_idea(idea: str) -> str:
    """Nombre corto y legible derivado de la frase hablada."""
    return slugify("-".join(keywords(idea, 3)))


_FILLER = {
    "creame", "crea", "crear", "haz", "hazme", "necesito", "quiero", "construye",
    "construir", "genera", "hacer", "una", "un", "unos", "unas", "la", "el", "los",
    "las", "de", "del", "que", "con", "para", "por", "app", "apps", "aplicacion",
    "aplicaciones", "programa", "software", "nueva", "nuevo", "pequena", "pequeno",
}


def keywords(idea: str, limit: int = 4) -> list[str]:
    """Palabras con sustancia de ``"créame una app de notas minimalista"``."""
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", (idea or "").lower()).encode("ascii", "ignore").decode())
    picked = [word for word in words if word not in _FILLER and len(word) > 2]
    return picked[:limit] or words[:limit]


def idea_to_title(idea: str, slug: str) -> str:
    """Título legible para la cabecera de la app generada (sin la sarta de preliminares)."""
    words = keywords(idea, 4)
    if not words:
        return slug.replace("-", " ").title()
    return " ".join(word.capitalize() for word in words)


def project_dir(projects_root: Path, slug: str) -> Path:
    """Carpeta libre del proyecto, añadiendo ``-2``, ``-3``… si existe."""
    base = Path(projects_root) / slug
    if not base.exists():
        return base
    for index in range(2, 500):
        candidate = Path(projects_root) / f"{slug}-{index}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("demasiadas variantes del proyecto")


@dataclass(frozen=True)
class BuildFile:
    """Un archivo del manifiesto, validado y listo para escribir."""

    path: str
    content: str

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix.lower()


def parse_build_manifest(text: str) -> tuple[list[BuildFile], dict[str, str], str]:
    """``(archivos, meta, error)`` a partir de la respuesta del coder."""
    payload, error = _loads(text)
    if error:
        return [], {}, error
    raw = payload.get("files") or payload.get("archivos") or []
    if isinstance(raw, dict):
        raw = [{"path": key, "content": value} for key, value in raw.items()]
    files: list[BuildFile] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("ruta") or "").strip().replace("\\", "/")
        while path.startswith("./"):  # sólo el prefijo "./"; los ".." deben sobrevivir para ser rechazados
            path = path[2:]
        content = item.get("content") if isinstance(item.get("content"), str) else item.get("contenido")
        if not path or not isinstance(content, str) or not content.strip():
            continue
        files.append(BuildFile(path=path, content=content.replace("\r\n", "\n")))
    meta = {
        "name": str(payload.get("name") or "").strip(),
        "stack": str(payload.get("stack") or "").strip().lower(),
        "summary": str(payload.get("summary") or payload.get("resumen") or "").strip(),
        "entry": str(payload.get("entry") or "").strip(),
        "run": str(payload.get("run") or "").strip(),
    }
    return files, meta, ""


def _loads(text: str) -> tuple[dict[str, Any], str]:
    cleaned = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    for candidate in (cleaned, re.sub(r",\s*([}\]])", r"\1", cleaned)):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data, ""
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data, ""
        except (json.JSONDecodeError, TypeError):
            pass
    return {}, "el modelo no devolvió un manifiesto JSON válido"


def validate_files(files: Sequence[BuildFile], max_files: int = 40, max_bytes: int = 120_000) -> tuple[list[BuildFile], list[str]]:
    """Rechaza rutas/contenidos peligrosos antes de tocar disco."""
    try:
        import config

        settings = dict(getattr(config, "APP_BUILDER_CONFIG", {}))
        max_files = int(settings.get("max_files", max_files))
        max_bytes = int(settings.get("max_file_bytes", max_bytes))
        allowed = tuple(settings.get("allowed_extensions", ()))
    except Exception:  # pragma: no cover
        allowed = ()
    accepted: list[BuildFile] = []
    rejected: list[str] = []
    for item in files:
        path = Path(item.path)
        if path.is_absolute() or ".." in path.parts:
            rejected.append(f"{item.path}: ruta fuera del proyecto")
            continue
        if any(part.startswith(".") for part in path.parts[:-1]):
            rejected.append(f"{item.path}: directorios ocultos no permitidos")
            continue
        if path.name.lower() in {"killswitch.py", "config.py", "main.py"}:
            rejected.append(f"{item.path}: nombre reservado por EON")
            continue
        suffix = path.suffix.lower()
        if allowed and suffix not in allowed:
            rejected.append(f"{item.path}: extensión {suffix or '(ninguna)'} no permitida")
            continue
        if len(item.content.encode("utf-8")) > max_bytes:
            rejected.append(f"{item.path}: demasiado grande")
            continue
        accepted.append(item)
    if len(accepted) > max_files:
        rejected.append(f"se recortó a los {max_files} primeros archivos")
        return accepted[:max_files], rejected
    return accepted, rejected


def verify_files(files: Sequence[BuildFile]) -> list[str]:
    """Comprobación de sintaxis/estructura sin ejecutar nada del usuario."""
    problems: list[str] = []
    for item in files:
        suffix = item.suffix
        if suffix == ".py":
            try:
                compile(item.content, item.path, "exec")
            except SyntaxError as exc:
                problems.append(f"{item.path}: sintaxis Python en la línea {exc.lineno}")
        elif suffix == ".html":
            lowered = item.content.lower()
            for tag in ("<!doctype html", "<html", "<body"):
                if tag not in lowered:
                    problems.append(f"{item.path}: falta {tag}>")
        elif suffix == ".js":
            if item.content.count("{") != item.content.count("}"):
                problems.append(f"{item.path}: llaves desbalanceadas")
            if item.content.count("(") != item.content.count(")"):
                problems.append(f"{item.path}: paréntesis desbalanceados")
        elif suffix == ".json":
            try:
                json.loads(item.content)
            except ValueError as exc:
                problems.append(f"{item.path}: JSON inválido ({exc})")
        elif suffix == ".css" and item.content.count("{") != item.content.count("}"):
            problems.append(f"{item.path}: llaves desbalanceadas")
    names = {item.path for item in files}
    if not any(name.endswith((".html", ".py")) for name in names):
        problems.append("no hay punto de entrada (ni index.html ni app.py)")
    return problems


def template_files(title: str, slug: str, stack: str) -> list[BuildFile]:
    """App completa de respaldo cuando no hay modelo disponible."""
    if stack == "python":
        return [BuildFile(path="app.py", content=_PYTHON_APP.replace("{{TITLE}}", title).replace("{{SLUG}}", slug))]
    return [
        BuildFile(path="index.html", content=_WEB_HTML.replace("{{TITLE}}", title)),
        BuildFile(path="style.css", content=_WEB_CSS),
        BuildFile(path="app.js", content=_WEB_JS),
        BuildFile(path="README.md", content=f"# {title}\n\nGenerada por {BRAND}. Abre `index.html` o usa el botón de EON.\n"),
    ]


# --------------------------------------------------------------------------- #
# Servidor local para previsualizar
# --------------------------------------------------------------------------- #


class _QuietHandler(BaseHTTPRequestHandler):
    """Sirve la carpeta del proyecto sin ensuciar el registro con cada petición."""

    root: Path = Path(".")

    def do_GET(self) -> None:
        relative = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:  # intento de escapar con ../
            self.send_error(403, "fuera del proyecto")
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.exists():
            self.send_error(404, "no existe")
            return
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _content_type(candidate.suffix))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _content_type(suffix: str) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".ico": "image/x-icon",
    }.get(suffix.lower(), "application/octet-stream")


class PreviewServer:
    """HTTP local de un solo proyecto, en un puerto del rango configurado.

    Se usa en lugar de ``file://`` porque los navegadores en modo ``--app``
    tratan peor los ``file://`` (localStorage aislado, CORS para módulos ES).
    """

    def __init__(self, directory: Path, logger: logging.Logger | None = None) -> None:
        self.directory = Path(directory)
        self.log = logger or log
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port = 0

    def start(self) -> int | None:
        try:
            import config

            low, high = (getattr(config, "APP_BUILDER_CONFIG", {}) or {}).get("preview_port_range", (8765, 8790))
        except Exception:  # pragma: no cover
            low, high = 8765, 8790
        for port in range(int(low), int(high) + 1):
            try:
                handler = type("Bound", (_QuietHandler,), {"root": self.directory})
                self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            except OSError:
                continue
            self.port = port
            self.thread = threading.Thread(target=self.server.serve_forever, name="eon-preview-server", daemon=True)
            self.thread.start()
            self.log.info("previsualización en http://127.0.0.1:%d", port)
            return port
        self.log.warning("sin puertos libres para la previsualización")
        return None

    def url(self, entry: str = "index.html") -> str:
        if not self.port:
            return ""
        return f"http://127.0.0.1:{self.port}/{entry.lstrip('/')}"

    def stop(self) -> None:
        server, self.server = self.server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # pragma: no cover
                pass
        thread, self.thread = self.thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)


# --------------------------------------------------------------------------- #
# Fábrica
# --------------------------------------------------------------------------- #


@dataclass
class BuildResult:
    """Resultado de construir y lanzar una app."""

    ok: bool
    name: str = ""
    slug: str = ""
    directory: str = ""
    entry: str = ""
    stack: str = "web"
    files: tuple[str, ...] = ()
    detail: str = ""
    launched: bool = False
    used_template: bool = False
    url: str = ""
    problems: tuple[str, ...] = ()

    def message(self) -> str:
        if self.ok and self.launched:
            return "Señor, su aplicación está terminada y abierta en su pantalla."
        if self.ok:
            return f"Su aplicación está lista en {self.directory}, pero no pude abrirla sola."
        return self.detail or "No pude construir la aplicación."


@dataclass
class AppBuilder:
    """Crea, verifica y lanza proyectos generados por voz."""

    router: Any | None = None
    projects_root: Path | None = None
    notify: Callable[[str], None] | None = None
    on_state: Callable[[str, dict], None] | None = None
    logger: logging.Logger | None = None
    serve_preview: bool = True

    def __post_init__(self) -> None:
        self.log = self.logger or log
        if self.projects_root is None:
            try:
                import config

                self.projects_root = Path(config.PROJECTS_DIR)
            except Exception:  # pragma: no cover
                self.projects_root = Path.cwd() / "workspace" / "projects"
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._busy = False
        self._servers: list[PreviewServer] = []
        self.history: list[BuildResult] = []

    @property
    def busy(self) -> bool:
        return self._busy

    # ------------------------------------------------------------------ API --
    def create(self, idea: str, stack: str = "auto", name: str = "") -> BuildResult:
        """Idea en español -> carpeta de proyecto verificada (y abierta si se puede)."""
        idea = (idea or "").strip()
        if not idea:
            return BuildResult(False, detail="No he oído la idea de la aplicación.")
        with self._lock:
            if self._busy:
                return BuildResult(False, detail="Estoy terminando otra aplicación; espídame un momento.")
            self._busy = True
        try:
            result = self._create_locked(idea, stack, name)
            self.history.append(result)
            return result
        except Exception as exc:  # una app fallida no puede tumbar a EON
            self.log.exception("fábrica de apps abortada")
            return BuildResult(False, detail=f"Fallo interno al construir: {exc}")
        finally:
            with self._lock:
                self._busy = False

    def list_projects(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for folder in sorted(self.projects_root.iterdir()) if self.projects_root.exists() else []:
            if not folder.is_dir():
                continue
            entry = next((item for item in ("index.html", "app.py") if (folder / item).exists()), "")
            out.append(
                {
                    "name": folder.name,
                    "path": str(folder),
                    "entry": entry,
                    "files": sum(1 for _ in folder.rglob("*") if _.is_file()),
                    "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(folder.stat().st_ctime)),
                }
            )
        return out

    def open_existing(self, slug: str) -> BuildResult:
        """Vuelve a abrir un proyecto ya construido."""
        folder = self.projects_root / slugify(slug)
        if not folder.exists():
            matches = [item for item in self.list_projects() if slugify(slug) in item["name"]]
            if not matches:
                return BuildResult(False, detail=f"No encuentro ningún proyecto llamado {slug}")
            folder = Path(matches[0]["path"])
        entry = "app.py" if (folder / "app.py").exists() else "index.html"
        result = BuildResult(True, name=folder.name, slug=folder.name, directory=str(folder), entry=entry)
        self._launch(result, folder, entry)
        return result

    # --------------------------------------------------------------- interno --
    def _create_locked(self, idea: str, stack: str, name: str) -> BuildResult:
        self._emit("thinking", {"idea": idea[:140]})
        files, meta, error = self._generate(idea)
        used_template = False
        if not files:
            self.log.info("usando plantilla local (%s)", error or "sin modelo")
            stack_choice = self._pick_stack(idea, stack, meta.get("stack", ""))
            slug = slugify(name) if name else slug_from_idea(idea)
            files = template_files(idea_to_title(name or idea, slug), slug, stack_choice)
            meta = {"name": slug, "stack": stack_choice, "summary": idea[:120], "entry": "", "run": ""}
            used_template = True
        stack_choice = self._pick_stack(idea, stack, meta.get("stack", ""))
        slug = slugify(name or meta.get("name")) if (name or meta.get("name")) else slug_from_idea(idea)
        directory = project_dir(self.projects_root, slug)
        accepted, rejected = validate_files(files)
        if not accepted:
            return BuildResult(False, detail="; ".join(rejected) or "el manifiesto no tenía archivos válidos", stack=stack_choice)
        accepted = self._ensure_entry(accepted, stack_choice, idea_to_title(name or idea, slug), slug)
        problems = verify_files(accepted)
        if problems:
            self.log.warning("problemas en la app generada: %s", problems)
        directory.mkdir(parents=True, exist_ok=True)
        written = self._write(directory, accepted)
        self._write_manifest(directory, slug, idea, meta, written, problems)
        result = BuildResult(
            ok=not problems,
            name=meta.get("name") or slug,
            slug=slug,
            directory=str(directory),
            entry=self._entry_of(accepted, stack_choice),
            stack=stack_choice,
            files=tuple(written),
            used_template=used_template,
            problems=tuple(problems),
            detail=" · ".join(rejected) if rejected else "",
        )
        if not problems:
            self._install_dependencies(directory, meta)
        self._launch(result, directory, result.entry)
        self._emit("built", {"path": result.directory, "files": len(written), "launched": result.launched})
        return result

    def _generate(self, idea: str) -> tuple[list[BuildFile], dict[str, str], str]:
        if self.router is None:
            return [], {}, "sin motor de modelos"
        prompt = (
            f"Idea del usuario: {idea}\n"
            "Contexto: se ejecutará en Windows 11 sin conexión a internet; sólo la biblioteca "
            "estándar de Python o HTML/CSS/JS puro. Interfaz en español, tema oscuro, acento #00e5ff.\n"
            "Devuelve el manifiesto JSON completo."
        )
        try:
            result = self.router.generate(prompt, role="coder", system=CODEGEN_SYSTEM, options={"temperature": 0.25, "num_predict": 3200, "num_ctx": 8192})
        except Exception as exc:
            return [], {}, f"el modelo de código no respondió: {exc}"
        return parse_build_manifest(result.text)

    @staticmethod
    def _pick_stack(idea: str, requested: str, from_model: str) -> str:
        """Elige stack: petición explícita > modelo > heurística de la frase."""
        for candidate in (requested, from_model):
            value = (candidate or "").strip().lower()
            if value in ("web", "python", "tkinter"):
                return "python" if value == "tkinter" else value
        text = (idea or "").lower()
        if any(word in text for word in ("ventana", "escritorio", "script", "automatiza", ".py", "python")):
            return "python"
        return "web"

    @staticmethod
    def _ensure_entry(files: Sequence[BuildFile], stack: str, title: str, slug: str) -> list[BuildFile]:
        """Garantiza que siempre haya algo que abrir, aunque el modelo lo olvide."""
        pool = list(files)
        names = {item.path.lower() for item in pool}
        if stack == "python":
            if not any(name.endswith(".py") for name in names):
                pool.append(BuildFile("app.py", _PYTHON_APP.replace("{{TITLE}}", title).replace("{{SLUG}}", slug)))
            return pool
        if not any(name.endswith(".html") for name in names):
            fallback = template_files(title, slug, "web")
            existing = {item.path.lower() for item in pool}
            pool.extend(item for item in fallback if item.path.lower() not in existing)
        return pool

    @staticmethod
    def _entry_of(files: Sequence[BuildFile], stack: str) -> str:
        names = [item.path for item in files]
        preferred = "app.py" if stack == "python" else "index.html"
        for name in names:
            if name.lower().endswith(preferred):
                return name
        for name in names:
            if name.lower().endswith((".html", ".py")):
                return name
        return preferred if preferred in names else (names[0] if names else "")

    @staticmethod
    def _write(directory: Path, files: Sequence[BuildFile]) -> list[str]:
        written: list[str] = []
        for item in files:
            target = directory / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8")
            written.append(item.path)
        return written

    def _write_manifest(self, directory: Path, slug: str, idea: str, meta: dict[str, str], files: Sequence[str], problems: Sequence[str]) -> None:
        payload = {
            "builder": BRAND,
            "slug": slug,
            "idea": idea,
            "summary": meta.get("summary", ""),
            "stack": meta.get("stack", ""),
            "entry": self._entry_of([BuildFile(path=name, content="") for name in files], meta.get("stack", "web")),
            "files": list(files),
            "problems": list(problems),
            "created": datetime_iso(),
        }
        try:
            (directory / ".eon-app.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self.log.debug("no se pudo escribir el manifiesto del proyecto: %s", exc)

    def _install_dependencies(self, directory: Path, meta: dict[str, str]) -> None:
        """Sólo si el manifiesto lo pide y la config lo permite: nunca por defecto."""
        requirements = directory / "requirements.txt"
        if not requirements.exists():
            return
        try:
            import config

            if not bool(getattr(config, "APP_BUILDER_CONFIG", {}).get("install_deps", False)):
                self.log.info("dependencias de %s sin instalar (install_deps=False)", directory.name)
                return
        except Exception:  # pragma: no cover
            return
        started = time.time()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(requirements), "--disable-pip-version-check"],
                capture_output=True, text=True, timeout=600, check=False, cwd=str(directory),
            )
            if result.returncode != 0:
                self.log.warning("pip terminó con %s: %s", result.returncode, (result.stderr or "")[-200:])
        except (OSError, subprocess.SubprocessError) as exc:
            self.log.warning("no se pudieron instalar dependencias: %s", exc)
        finally:
            self.log.debug("instalación de dependencias en %.1f s", time.time() - started)

    # ------------------------------------------------------------------ abrir --
    def _launch(self, result: BuildResult, directory: Path, entry: str) -> None:
        """Abre la app: navegador para web, intérprete para python."""
        if not entry:
            return
        try:
            if entry.endswith(".py"):
                result.launched = self._launch_python(directory, entry)
                return
            result.launched = self._launch_web(directory, entry, result)
        except Exception as exc:
            self.log.debug("no se pudo abrir la app: %s", exc)

    def _launch_python(self, directory: Path, entry: str) -> bool:
        try:
            flags = 0
            if sys.platform == "win32":
                flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(
                [python_executable_for_gui(), str(directory / entry)],
                cwd=str(directory),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            return True
        except (OSError, ValueError) as exc:
            self.log.warning("no se pudo lanzar %s: %s", entry, exc)
            return False

    def _launch_web(self, directory: Path, entry: str, result: BuildResult) -> bool:
        server: PreviewServer | None = None
        if self.serve_preview:
            server = PreviewServer(directory, logger=self.log)
            port = server.start()
            if port:
                self._servers.append(server)
                result.url = server.url(entry)
        if result.url:
            opened = self._open_url(result.url)
            if opened:
                return True
        target = directory / entry
        if sys.platform == "win32":  # abrir el archivo tal cual, con su handler
            try:
                import os

                os.startfile(str(target))  # type: ignore[attr-defined]
                result.url = str(target)
                return True
            except (OSError, AttributeError) as exc:
                self.log.debug("startfile falló: %s", exc)
        return self._open_url(target.as_uri())

    def _open_url(self, url: str) -> bool:
        """Abre en Comet si existe (navegador favorito de Pablo) y si no, en el sistema."""
        try:
            from core.acoustic_detector import browser_app_command, default_browser, find_comet

            browser = find_comet() or default_browser()
            if browser is not None:
                command = browser_app_command(browser, url)
                if command:
                    try:
                        flags = getattr(subprocess, "DETACHED_PROCESS", 0) if sys.platform == "win32" else 0
                        subprocess.Popen(list(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                        return True
                    except (OSError, ValueError) as exc:
                        self.log.debug("lanzamiento directo falló: %s", exc)
        except Exception as exc:
            self.log.debug("no se pudo resolver el navegador: %s", exc)
        try:
            from core.acoustic_detector import open_with_default

            return open_with_default(url)
        except Exception:
            return False

    def stop_servers(self) -> None:
        for server in self._servers:
            try:
                server.stop()
            except Exception:  # pragma: no cover
                pass
        self._servers.clear()

    def _notify(self, text: str) -> None:
        if self.notify is None:
            return
        try:
            self.notify(text)
        except Exception:
            pass

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_state is None:
            return
        try:
            self.on_state(name, payload)
        except Exception:
            pass


def datetime_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def python_executable_for_gui() -> str:
    """``pythonw.exe`` si existe (ventana sin consola); si no, el intérprete actual."""
    current = Path(sys.executable)
    sibling = current.with_name("pythonw.exe")
    if sys.platform == "win32" and sibling.exists():
        return str(sibling)
    return str(current)
