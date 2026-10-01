"""Visión de escritorio y actuación sobre ratón/teclado (spec 4.3).

Flujo de un comando del tipo «haz clic en el botón azul de la izquierda»:

1. ``mss`` captura el frame limpio del monitor pedido (con reescalado que
   preserva la nitidez del texto de interfaz, clave para el modelo de visión).
2. Se envía a ``llama3.2-vision`` con un *system prompt* de grounding que exige
   JSON estricto: ``{"action": "click", "x": 1240, "y": 530, "description": ...}``.
3. Se normalizan las coordenadas (el modelo puede devolver píxeles absolutos,
   unidades 0-1 o 0-1000) y se recortan al borde de la pantalla.
4. El ratón viaja por una curva de Bézier humanizada y se pulsa; el teclado se
   escribe con retardo variable por carácter.
5. ``gui/screen_glow.py`` enciende el borde cian durante todo el ciclo, y el
   kill switch puede cortar en cualquier momento entre acción y acción.

Nada de esto requiere permisos especiales ni dependencias duras: si ``mss`` o
``pyautogui`` faltan, hay respaldos con ``PyQt6`` y la API Win32.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("eon.vision")

VALID_ACTIONS = {"click", "double_click", "right_click", "move", "type", "key", "scroll", "wait", "screenshot", "describe", "stop"}
DESTRUCTIVE = {"type", "key", "click", "double_click", "right_click", "scroll"}

_GROUNDING_SYSTEM = """Eres el módulo de visión de EON, un agente de escritorio en Windows.
Recibes UNA captura de pantalla y una orden del usuario. Devuelves SÓLO JSON válido,
sin prosa y sin cercas de código, con esta forma:

{"actions": [{"action": "click", "x": 1240, "y": 530, "description": "botón Comprar ahora"}, ...]}

Reglas:
- Las coordenadas x,y son píxeles ENTEROS sobre el tamaño de imagen indicado, con (0,0)
  en la esquina superior izquierda. Nunca normalices tú: devuelve píxeles de la imagen.
- Usa exactamente un paso por cada interacción. Acciones admitidas:
  click, double_click, right_click, move, type, key, scroll, wait, describe, stop.
- "type" lleva "text"; "key" lleva "keys" (p. ej. ["ctrl","s"]); "scroll" lleva "amount"
  (positivo = arriba); "wait" lleva "ms"; "describe" lleva "text" con lo que ves.
- Si la orden no se puede cumplir con esta pantalla, devuelve
  {"actions": [], "error": "motivo breve en español"}.
- Antes de hacer clic confirma que el elemento está visible. Si no lo ves, no lo inventes:
  devuelve un paso "describe" explicando qué falta.
- Si el usuario corrigió ("no, el de la izquierda"), vuelve a mirar la imagen y corrige.
"""

_JSON_ARRAY = re.compile(r"\[[\s\S]*\]")
_JSON_OBJECT = re.compile(r"\{[\s\S]*\}")


# --------------------------------------------------------------------------- #
# Modelos de datos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Action:
    """Un paso de automatización ya normalizado a coordenadas de pantalla."""

    action: str
    x: int = 0
    y: int = 0
    text: str = ""
    keys: tuple[str, ...] = ()
    button: str = "left"
    amount: int = 0
    ms: int = 0
    description: str = ""
    confidence: float = 0.0

    @property
    def destructive(self) -> bool:
        return self.action in DESTRUCTIVE

    def label(self) -> str:
        if self.description:
            return self.description[:64]
        if self.action == "type":
            return f"escribir {len(self.text)} caracteres"
        if self.action == "key":
            return "+".join(self.keys) or "combinación"
        if self.action == "click":
            return f"clic en ({self.x}, {self.y})"
        return self.action


@dataclass(frozen=True)
class Screen:
    """Captura de pantalla lista para mandar al modelo."""

    path: Path
    image_b64: str
    width: int
    height: int
    scale: float = 1.0
    monitor: int = 0

    @property
    def original_size(self) -> tuple[int, int]:
        if self.scale in (0.0, 1.0):
            return (self.width, self.height)
        return (int(round(self.width / self.scale)), int(round(self.height / self.scale)))


@dataclass
class ExecutionReport:
    """Resultado de ejecutar un plan: lo consume la GUI y el registro."""

    ok: bool
    executed: int = 0
    skipped: int = 0
    aborted: str = ""
    error: str = ""
    actions: tuple[Action, ...] = ()
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def summary(self) -> str:
        if self.ok:
            return f"{self.executed} paso(s) ejecutados" + (f", {self.skipped} omitido(s)" if self.skipped else "")
        if self.aborted:
            return f"interrumpido: {self.aborted}"
        return self.error or "no se pudo ejecutar"


# --------------------------------------------------------------------------- #
# Análisis de la respuesta del modelo (puro y testeable)
# --------------------------------------------------------------------------- #


def parse_plan(
    raw: str,
    screen_size: tuple[int, int] | None = None,
    max_steps: int = 12,
    coord_space: str | None = None,
) -> tuple[list[Action], str]:
    """Convierte la salida del VLM en una lista de :class:`Action`.

    Tolera cercas de código, un único objeto en vez de lista, claves en inglés o
    en español, y coordenadas normalizadas (0-1 ó 0-1000). Devuelve
    ``(acciones, error)``; nunca lanza, porque un alucinó del modelo es un caso
    esperado, no una excepción.
    """
    payload, error = _load_payload(raw)
    if error:
        return [], error
    if isinstance(payload, dict):
        error = str(payload.get("error") or "").strip()
        items = payload.get("actions") or payload.get("steps") or payload.get("pasos") or []
        if not items and ("action" in payload or "x" in payload):
            items = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        return [], "respuesta del modelo sin formato esperado"
    actions: list[Action] = []
    for item in items[: max(1, int(max_steps))]:
        action = parse_action(item, screen_size, coord_space)
        if action is not None:
            actions.append(action)
    if not actions and error:
        return [], error
    if not actions:
        return [], "el modelo no propuso ningún paso accionable"
    return actions, ""


def parse_action(item: Any, screen_size: tuple[int, int] | None = None, coord_space: str | None = None) -> Action | None:
    """Normaliza un paso suelto; ``None`` si es inválido."""
    if isinstance(item, str):
        item = {"action": item}
    if not isinstance(item, dict):
        return None
    name = str(item.get("action") or item.get("type") or item.get("accion") or "").strip().lower()
    aliases = {
        "left_click": "click",
        "clic": "click",
        "click_left": "click",
        "doble_clic": "double_click",
        "doubleclick": "double_click",
        "clic_derecho": "right_click",
        "rightclick": "right_click",
        "escribir": "type",
        "write": "type",
        "text": "type",
        "tecla": "key",
        "hotkey": "key",
        "wheel": "scroll",
        "espera": "wait",
        "sleep": "wait",
        "captura": "screenshot",
        "mirar": "describe",
        "observar": "describe",
        "terminar": "stop",
        "fin": "stop",
    }
    name = aliases.get(name, name)
    if name not in VALID_ACTIONS:
        return None
    x_raw = _first(item, ("x", "coord_x", "cx", "pos_x"))
    y_raw = _first(item, ("y", "coord_y", "cy", "pos_y"))
    x, y = normalize_point(x_raw, y_raw, screen_size or (1920, 1080), coord_space)
    keys = _coerce_keys(_first(item, ("keys", "key", "kombos", "combinacion")))
    text = str(_first(item, ("text", "texto", "value", "content")) or "")
    button = str(_first(item, ("button", "boton")) or "left").lower()
    if button not in ("left", "right", "middle"):
        button = "left"
    amount = _int(_first(item, ("amount", "clicks", "delta", "scroll")), 0)
    ms = _int(_first(item, ("ms", "duration", "wait_ms")), 0)
    confidence = float(_first(item, ("confidence", "score")) or 0.0)
    description = str(_first(item, ("description", "desc", "objetivo", "target")) or "")
    return Action(
        action=name,
        x=x,
        y=y,
        text=text,
        keys=keys,
        button=button,
        amount=amount,
        ms=ms,
        description=description,
        confidence=max(0.0, min(1.0, confidence if confidence == confidence else 0.0)),
    )


def normalize_point(
    x: Any,
    y: Any,
    size: tuple[int, int],
    coord_space: str | None = None,
) -> tuple[int, int]:
    """Lleva coordenadas del modelo a píxeles de pantalla.

    Tres convenciones posibles: fracciones ``0..1``, píxeles absolutos y la
    escala ``0..1000`` que usan algunos modelos de grounding. El modo
    ``"auto"`` (por defecto) resuelve la ambigüedad así:

    * ambos componentes <= 1.0001  -> fracción del frame;
    * cualquier componente > 1000   -> forzosamente píxeles (0-1000 no puede);
    * en otro caso, píxeles, que es lo que pide nuestro *system prompt*.

    Para un modelo que sí emita 0-1000 se fija ``VISION_COORD_SPACE =
    "normalized1000"`` en ``config.py``; adivinarlo por heurística era justo el
    fallo que desplazaba un clic del ``x=10`` real a ``x=19``.
    """
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    fx, fy = _float(x), _float(y)
    if fx is None or fy is None:
        return 0, 0
    space = (coord_space or _coord_space()).lower()
    if max(abs(fx), abs(fy)) <= 1.0001:
        px, py = fx * width, fy * height
    elif space == "normalized1000":
        px, py = fx / 1000.0 * width, fy / 1000.0 * height
    elif space == "auto" and max(abs(fx), abs(fy)) > 1000.0:
        px, py = fx, fy
    else:
        px, py = fx, fy
    return int(min(max(0.0, px), width - 1.0)), int(min(max(0.0, py), height - 1.0))


def _coord_space() -> str:
    try:
        import config

        return str(getattr(config, "VISION_COORD_SPACE", "auto"))
    except Exception:  # pragma: no cover - config siempre está
        return "auto"


def _load_payload(raw: str) -> tuple[Any, str]:
    text = (raw or "").strip()
    if not text:
        return None, "respuesta vacía del modelo de visión"
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    for candidate in _candidates(text):
        try:
            return json.loads(candidate), ""
        except (json.JSONDecodeError, ValueError):
            continue
    return None, "no se pudo interpretar el JSON del modelo"


def _candidates(text: str) -> list[str]:
    """Formas JSON a tentar, en orden de especificidad.

    El objeto va antes que el array: la respuesta canónica del modelo es
    ``{"actions": [...], "error": ...}`` y ese ``error`` se pierde si el
    recorte greedy de la expresión regular se queda antes en el ``[]``.
    """
    out = [text]
    for pattern in (_JSON_OBJECT, _JSON_ARRAY):
        match = pattern.search(text)
        if match:
            out.append(match.group(0))
    out.append(re.sub(r",\s*([}\]])", r"\1", text))
    return [item for item in out if item]


def _first(mapping: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _coerce_keys(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip().lower() for item in value if str(item).strip())
    text = str(value).strip().lower()
    for separator in ("+", "-", " "):
        if separator in text:
            return tuple(part for part in text.split(separator) if part)
    return (text,)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Captura
# --------------------------------------------------------------------------- #


class ScreenCapture:
    """Captura de pantalla con ``mss`` y reescalado que respeta el texto de UI.

    El reescalado importa más de lo que parece: los VLM pierden los botones
    pequeños si se les manda la pantalla entera a escala rara. Se limita el lado
    mayor a ``max_width`` (1280 por defecto) y se devuelve el factor para poder
    desescalar las coordenadas.
    """

    def __init__(
        self,
        max_width: int = 1280,
        jpeg_quality: int = 82,
        directory: Path | None = None,
        keep: int = 12,
        logger: logging.Logger | None = None,
    ) -> None:
        self.max_width = int(max_width)
        self.jpeg_quality = int(jpeg_quality)
        self.log = logger or log
        self.directory = directory or Path(tempfile.gettempdir()) / "eon-shots"
        self.keep = int(keep)
        self._sct = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API --
    def grab(self, monitor: int = 0, full_desktop: bool = False) -> Screen | None:
        """Captura el monitor indicado (0 = todos) y lo devuelve en base64."""
        path = self._next_path()
        try:
            png = self._grab_png(monitor, full_desktop)
        except Exception as exc:
            self.log.warning("captura falló: %s", exc)
            return None
        if png is None:
            return None
        try:
            payload, width, height, scale = self._prepare(png)
            path.write_bytes(payload)
            self._prune()
            return Screen(
                path=path,
                image_b64=base64.b64encode(payload).decode("ascii"),
                width=width,
                height=height,
                scale=scale,
                monitor=monitor,
            )
        except Exception as exc:
            self.log.debug("no se pudo preparar la captura: %s", exc)
            return None

    def describe_state(self) -> dict[str, Any]:
        available = self._backend()
        return {"backend": available or "ninguno", "directory": str(self.directory)}

    # -------------------------------------------------------------- interno --
    def _backend(self) -> str:
        try:
            import mss  # type: ignore  # noqa: F401

            return "mss"
        except Exception:
            pass
        try:
            import PyQt6.QtWidgets  # type: ignore  # noqa: F401

            return "qt"
        except Exception:
            return ""

    def _grab_png(self, monitor: int, full_desktop: bool) -> bytes | None:
        backend = self._backend()
        if backend == "mss":
            return self._grab_mss(monitor, full_desktop)
        if backend == "qt":
            return self._grab_qt()
        if sys.platform == "win32":  # pragma: no cover - sólo Windows
            return self._grab_printscreen()
        return None

    def _grab_mss(self, monitor: int, full_desktop: bool) -> bytes | None:
        import io

        import mss  # type: ignore
        from PIL import Image  # type: ignore

        with self._lock:
            if self._sct is None:
                self._sct = mss.mss()
            monitors = self._sct.monitors
            if not monitors:
                return None
            if full_desktop or monitor <= 0 or monitor >= len(monitors):
                box = monitors[0]
            else:
                box = monitors[monitor]
            shot = self._sct.grab(box)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()

    def _grab_qt(self) -> bytes | None:  # pragma: no cover - requiere GUI
        from PyQt6.QtCore import QBuffer, QIODevice
        from PyQt6.QtGui import QGuiApplication

        window = QGuiApplication.primaryScreen().grabWindow(0)
        if window.isNull():
            return None
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        window.save(buffer, "PNG")
        data = bytes(buffer.data().data())
        buffer.close()
        return data or None

    def _grab_printscreen(self) -> bytes | None:  # pragma: no cover - sólo Windows
        """Último recurso sin dependencias: PowerShell .NET Capture."""
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height); "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
            "$tmp = Join-Path $env:TEMP 'eon-shot.png'; $bmp.Save($tmp); (Get-Item $tmp).Length"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=8, check=False
            )
            path = Path(tempfile.gettempdir()) / "eon-shot.png"
            return path.read_bytes() if path.exists() else None
        except Exception as exc:
            self.log.debug("captura por PowerShell falló: %s", exc)
            return None

    def _prepare(self, png: bytes) -> tuple[bytes, int, int, float]:
        """Devuelve ``(bytes_a_enviar, ancho, alto, escala)`` con nitidez usable."""
        try:
            import io

            from PIL import Image  # type: ignore

            image = Image.open(io.BytesIO(png)).convert("RGB")
            width, height = image.size
            scale = min(1.0, self.max_width / max(1, width))
            if scale < 1.0:
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                image = image.resize(new_size, Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=self.jpeg_quality, optimize=True)
            return buffer.getvalue(), image.width, image.height, scale
        except Exception as exc:
            self.log.debug("sin Pillow para reescalar (%s): se envía el PNG original", exc)
            return png, 0, 0, 1.0

    def _next_path(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory / f"shot-{int(time.time() * 1000) % 10_000_000}.jpg"

    def _prune(self) -> None:
        try:
            files = sorted(self.directory.glob("shot-*"), key=lambda item: item.stat().st_mtime, reverse=True)
            for stale in files[max(1, self.keep) :]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Entrada humanizada
# --------------------------------------------------------------------------- #


def bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    segments: int = 34,
    curvature: float = 0.22,
    jitter: float = 1.6,
    rng: random.Random | None = None,
) -> list[tuple[float, float]]:
    """Trayectoria con desaceleración en los extremos y un leve arco lateral.

    Un ratón humano no va en línea recta perfecta: el arco y el temblor evitan
    que la automatización se lea como un bot y, de paso, que la aceleración
    brusca del cursor moleste a Pablo.
    """
    generator = rng or random.Random(17)
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1.0:
        return [(x1, y1)]
    # vector perpendicular para desviar el punto de control
    px, py = -dy / length, dx / length
    bow = length * curvature * (1.0 if generator.random() > 0.5 else -1.0)
    cx = (x0 + x1) / 2.0 + px * bow
    cy = (y0 + y1) / 2.0 + py * bow
    steps = max(4, int(segments))
    points: list[tuple[float, float]] = []
    for index in range(steps + 1):
        t = index / steps
        eased = t * t * (3.0 - 2.0 * t)  # smoothstep: arranca y frena suave
        it = 1.0 - eased
        x = it * it * x0 + 2 * it * eased * cx + eased * eased * x1
        y = it * it * y0 + 2 * it * eased * cy + eased * eased * y1
        if 0 < index < steps:
            x += generator.uniform(-jitter, jitter)
            y += generator.uniform(-jitter, jitter)
        points.append((x, y))
    points[-1] = (x1, y1)
    return points


class InputActuator:
    """Movimiento y pulsación con backend preferido: pyautogui -> Win32 -> xdotool."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        type_delay: float | None = None,
        click_range: tuple[int, int] | None = None,
        segments: int | None = None,
        killswitch: Any | None = None,
    ) -> None:
        self.log = logger or log
        try:
            import config

            self.type_delay = float(type_delay if type_delay is not None else config.TYPE_CHAR_DELAY_S)
            self.click_range = tuple(click_range or config.CLICK_DURATION_RANGE_MS)  # type: ignore[arg-type]
            self.segments = int(segments or config.BEZIER_SEGMENTS)
        except Exception:  # pragma: no cover
            self.type_delay = 0.028
            self.click_range = (45, 110)
            self.segments = 34
        self.killswitch = killswitch
        self.backend = self._detect_backend()
        self._rng = random.Random(2026)
        self.position: tuple[float, float] = (0.0, 0.0)

    def _detect_backend(self) -> str:
        try:
            import pyautogui  # type: ignore

            pyautogui.FAILSAFE = True  # llevar el ratón a una esquina sigue siendo el "para" manual
            pyautogui.PAUSE = 0.0
            self._pyautogui = pyautogui
            return "pyautogui"
        except Exception as exc:
            self.log.debug("pyautogui no disponible: %s", exc)
        if sys.platform == "win32":
            return "win32"
        return "none"

    @property
    def available(self) -> bool:
        return self.backend != "none"

    # -------------------------------------------------------------- acciones --
    def move(self, x: int, y: int, humanize: bool = True) -> None:
        self._check()
        if humanize and self.backend == "pyautogui":
            for point in bezier_path(self.cursor_position(), (float(x), float(y)), self.segments, rng=self._rng):
                self._check()
                self._pyautogui.moveTo(point[0], point[1], duration=0.0)
                time.sleep(max(0.001, 0.008 + self._rng.random() * 0.004))
            self.position = (float(x), float(y))
            return
        self._warp(x, y)

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        self._check()
        self.move(x, y)
        time.sleep(0.03 + self._rng.random() * 0.05)
        if self.backend == "pyautogui":
            self._pyautogui.click(button=button, clicks=2 if double else 1)
        else:
            self._win32_click(button, double)
        self._check()

    def scroll(self, amount: int) -> None:
        self._check()
        if self.backend == "pyautogui":
            self._pyautogui.scroll(amount)
            return
        if sys.platform == "win32":  # pragma: no cover - sólo Windows
            self._win32_scroll(amount)

    def type_text(self, text: str) -> None:
        self._check()
        if not text:
            return
        if self.backend == "pyautogui":
            for char in text:
                self._check()
                self._pyautogui.typewrite(char) if char.isprintable() else None
                time.sleep(max(0.004, self.type_delay * (0.65 + self._rng.random() * 0.9)))
            return
        self._win32_type(text)

    def press_keys(self, keys: Sequence[str]) -> None:
        self._check()
        combo = "+".join(str(key) for key in keys if key)
        if not combo:
            return
        if self.backend == "pyautogui":
            self._pyautogui.hotkey(*keys)
            return
        self._win32_combo(keys)

    def wait(self, ms: int) -> None:
        remaining = max(0, int(ms)) / 1000.0
        step = 0.05
        while remaining > 0:
            self._check()
            nap = min(step, remaining)
            time.sleep(nap)
            remaining -= nap

    # -------------------------------------------------------------- interno --
    def _check(self) -> None:
        switch = self.killswitch
        if switch is not None and getattr(switch, "engaged", False):
            raise RuntimeError("kill switch activado: se aborta la actuación")

    def cursor_position(self) -> tuple[float, float]:
        if self.backend == "pyautogui":
            try:
                point = self._pyautogui.position()
                self.position = (float(point.x), float(point.y))
                return self.position
            except Exception:
                pass
        if sys.platform == "win32":  # pragma: no cover - sólo Windows
            try:
                import ctypes
                from ctypes import wintypes

                point = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                self.position = (float(point.x), float(point.y))
                return self.position
            except Exception:
                pass
        return self.position

    def _warp(self, x: int, y: int) -> None:
        if self.backend == "pyautogui":
            self._pyautogui.moveTo(x, y, duration=0.0)
            self.position = (float(x), float(y))
            return
        if sys.platform == "win32":  # pragma: no cover - sólo Windows
            try:
                import ctypes

                ctypes.windll.user32.SetCursorPos(int(x), int(y))
                self.position = (float(x), float(y))
                return
            except Exception as exc:
                self.log.debug("SetCursorPos falló: %s", exc)

    def _win32_click(self, button: str, double: bool) -> None:  # pragma: no cover - sólo Windows
        flags = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }
        down, up = flags.get(button, flags["left"])
        import ctypes

        user32 = ctypes.windll.user32
        rounds = 2 if double else 1
        for _ in range(rounds):
            hold_ms = self._rng.randint(*self.click_range) / 1000.0
            user32.mouse_event(down, 0, 0, 0, 0)
            time.sleep(max(0.02, hold_ms))
            user32.mouse_event(up, 0, 0, 0, 0)
            if double:
                time.sleep(0.05)

    def _win32_scroll(self, amount: int) -> None:  # pragma: no cover - sólo Windows
        import ctypes

        delta = int(amount) * 120
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta & 0xFFFFFFFF, 0)

    def _win32_type(self, text: str) -> None:  # pragma: no cover - sólo Windows
        """Escribe vía portapapeles: lo único fiable con acentos, ñ y emojis.

        El texto se pasa por ``stdin`` en lugar de intercalarlo en la línea de
        mandatos: así las comillas y los saltos de línea del contenido no pueden
        romper el comando ni colarse en él.
        """
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                input=text, capture_output=True, text=True, timeout=6, check=False,
            )
        except Exception as exc:
            self.log.debug("Set-Clipboard falló (%s); se prueba con el portapapeles de Qt", exc)
            if not self._clipboard_fallback(text):
                return
        self._win32_combo(("ctrl", "v"))

    def _clipboard_fallback(self, text: str) -> bool:
        """Portapapeles vía Qt, cuando PowerShell no está disponible."""
        try:  # pragma: no cover - requiere QGuiApplication viva
            from PyQt6.QtWidgets import QApplication

            QApplication.clipboard().setText(text)
            return True
        except Exception:
            return False

    def _win32_combo(self, keys: Sequence[str]) -> None:  # pragma: no cover - sólo Windows
        virtual = {
            "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
            "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
            "space": 0x20, "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
            "left": 0x25, "right": 0x27,
        }
        try:
            import ctypes

            user32 = ctypes.windll.user32
            pressed: list[int] = []
            for key in keys:
                code = virtual.get(str(key).lower())
                if code is None and len(str(key)) == 1:
                    code = ctypes.windll.user32.VkKeyScanW(str(key)) & 0xFF
                if code:
                    user32.keybd_event(code, 0, 0, 0)
                    pressed.append(code)
            time.sleep(0.03)
            for code in reversed(pressed):
                user32.keybd_event(code, 0, 0x0002, 0)
        except Exception as exc:
            self.log.debug("no se pudo enviar la combinación: %s", exc)


# --------------------------------------------------------------------------- #
# Orquestador
# --------------------------------------------------------------------------- #


class VisionActuator:
    """Ojo y mano de EON: mira la pantalla, planea, actúa y corrige en vuelo."""

    def __init__(
        self,
        router: Any | None = None,
        capture: ScreenCapture | None = None,
        actuator: InputActuator | None = None,
        bus: Any | None = None,
        killswitch: Any | None = None,
        glow: Any | None = None,
        on_state: Callable[[str, dict], None] | None = None,
        confirm: Callable[[Action], bool] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.router = router
        self.capture = capture or ScreenCapture(logger=logger or log)
        self.actuator = actuator or InputActuator(logger=logger or log, killswitch=killswitch)
        self.bus = bus
        self.killswitch = killswitch
        self.glow = glow
        self.on_state = on_state
        self.confirm = confirm
        self.log = logger or log
        try:
            import config

            self.confirm_mode = str(config.ACTION_CONFIRM_MODE)
            self.max_steps = int(config.MAX_ACTION_STEPS)
            # 0 = escritorio virtual completo (con mss); 1 = primer monitor.
            self.monitor = int(getattr(config, "VISION_MONITOR", 0))
        except Exception:  # pragma: no cover
            self.confirm_mode = "danger"
            self.max_steps = 12
            self.monitor = 0
        self._correction: str | None = None
        self._correction_lock = threading.Lock()
        self._running = threading.Event()
        self.last_screen: Screen | None = None
        self.actions_done = 0

    # ------------------------------------------------------------------ estado --
    @property
    def busy(self) -> bool:
        return self._running.is_set()

    def request_correction(self, text: str) -> bool:
        """Pablo habló a mitad de la automatización: se recongela y se re-planea."""
        if not self._running.is_set():
            return False
        with self._correction_lock:
            self._correction = (text or "").strip()
        self.log.info("corrección en vuelo recibida: %s", self._correction)
        return True

    def capabilities(self) -> dict[str, Any]:
        return {
            "capture": self.capture.describe_state()["backend"],
            "input": self.actuator.backend,
            "input_available": self.actuator.available,
            "confirm_mode": self.confirm_mode,
        }

    # --------------------------------------------------------------- visión --
    def look(self, prompt: str = "Describe lo que hay en pantalla de forma concisa.") -> str:
        """Una mirada: captura + ``llama3.2-vision`` + descarga inmediata del modelo."""
        screen = self._capture_with_glow("vision")
        if screen is None:
            return "No he podido capturar la pantalla."
        return self._ask_vision(prompt, screen)

    def plan(self, instruction: str, screen: Screen | None = None) -> tuple[list[Action], str, Screen | None]:
        """Pide al modelo un plan accionable. Devuelve ``(pasos, error, frame_usado)``."""
        screen = screen or self._capture_with_glow("vision")
        if screen is None:
            return [], "sin captura de pantalla", None
        raw = self._ask_vision(self._instruction_block(instruction), screen)
        actions, error = parse_plan(raw, (screen.width or 1920, screen.height or 1080), self.max_steps)
        return actions, error, screen

    def execute(self, actions: Sequence[Action]) -> ExecutionReport:
        """Ejecuta paso a paso, con comprobación del kill switch entre pasos."""
        started = time.perf_counter()
        executed = skipped = 0
        aborted = ""
        notes: list[str] = []
        self._running.set()
        if self.killswitch is not None:
            self.killswitch.note_actuator_active(True)
        try:
            for index, action in enumerate(actions, start=1):
                if self.killswitch is not None and self.killswitch.engaged:
                    aborted = "kill switch"
                    notes.append(f"interrumpido antes del paso {index}")
                    break
                correction = self._take_correction()
                if correction:
                    notes.append(f"re-planificado por: {correction[:60]}")
                    return self._replan(correction, actions[index:], started)
                if not self._allowed(action):
                    skipped += 1
                    notes.append(f"paso {index} requería confirmación: {action.label()}")
                    continue
                self._emit("action", {"index": index, "label": action.label(), "action": action.action})
                self._apply_glow("act")
                try:
                    self._perform(action)
                    executed += 1
                    self.actions_done += 1
                except Exception as exc:
                    notes.append(f"paso {index} falló: {exc}")
                    self.log.warning("no se pudo ejecutar %s: %s", action.action, exc)
                time.sleep(0.12 + 0.05 * (index % 3))
            ok = executed > 0 and not aborted
            return ExecutionReport(
                ok=ok, executed=executed, skipped=skipped, aborted=aborted,
                error="" if ok else (aborted or "nada ejecutado"), notes=notes,
                actions=tuple(actions), duration_s=time.perf_counter() - started,
            )
        finally:
            self._running.clear()
            if self.killswitch is not None:
                self.killswitch.note_actuator_active(False)
            self._apply_glow("off")

    def run(self, instruction: str) -> ExecutionReport:
        """«Mira, planea y hazlo» en una llamada (lo usa el orquestador de voz)."""
        started = time.perf_counter()
        self._emit("planning", {"instruction": instruction[:140]})
        actions, error, _screen = self.plan(instruction)
        if not actions:
            return ExecutionReport(ok=False, error=error or "sin pasos", duration_s=time.perf_counter() - started)
        self._emit("planned", {"steps": len(actions), "first": actions[0].label()})
        report = self.execute(actions)
        report.duration_s = time.perf_counter() - started
        return report

    # -------------------------------------------------------------- interno --
    def _perform(self, action: Action) -> None:
        if action.action in ("describe", "screenshot", "stop"):
            return
        if action.action in ("click", "double_click", "right_click"):
            self.actuator.click(action.x, action.y, "right" if action.action == "right_click" else action.button, double=action.action == "double_click")
        elif action.action == "move":
            self.actuator.move(action.x, action.y)
        elif action.action == "type":
            self.actuator.type_text(action.text)
        elif action.action == "key":
            self.actuator.press_keys(action.keys)
        elif action.action == "scroll":
            self.actuator.scroll(action.amount or 3)
        elif action.action == "wait":
            self.actuator.wait(action.ms or 400)

    def _ask_vision(self, prompt: str, screen: Screen) -> str:
        if self.router is None:
            return "El motor de modelos no está disponible."
        try:
            result = self.router.chat(
                [{"role": "user", "content": prompt}],
                role="vision",
                system=_GROUNDING_SYSTEM,
                images=[screen.image_b64],
                options={"temperature": 0.0, "num_predict": 420, "num_ctx": 4096},
            )
            return (result.text or "").strip()
        except Exception as exc:
            self.log.warning("el modelo de visión no respondió: %s", exc)
            self._emit("vision-error", {"detail": str(exc)[:180]})
            return ""

    def _instruction_block(self, instruction: str) -> str:
        size = f"{self.last_screen.width}x{self.last_screen.height}" if self.last_screen else "desconocido"
        return (
            f"Imagen: escritorio de Windows, {size} px.\n"
            f"Orden del usuario: {instruction}\n"
            "Devuelve el plan en JSON exactamente como se pide en las instrucciones del sistema."
        )

    def _capture_with_glow(self, mode: str) -> Screen | None:
        self._apply_glow(mode)
        try:
            screen = self.capture.grab(self.monitor)
        finally:
            if mode == "vision":
                self._apply_glow("off")
        if screen is not None:
            self.last_screen = screen
        else:
            self._emit("capture-error", {})
        return screen

    def _take_correction(self) -> str | None:
        with self._correction_lock:
            value, self._correction = self._correction, None
        return value

    def _replan(self, correction: str, remaining: Sequence[Action], started: float) -> ExecutionReport:
        """Congela la cola, vuelve a mirar y recalcula el objetivo del ratón."""
        self._emit("replanning", {"correction": correction[:120]})
        screen = self._capture_with_glow("vision")
        instruction = (
            f"Corrección del usuario: {correction}. Pasos ya hechos: {len(remaining)} restantes. "
            "Recalcula sólo lo que queda por hacer, con coordenadas nuevas sobre esta imagen."
        )
        actions, error, _ = self.plan(instruction, screen)
        if not actions:
            return ExecutionReport(
                ok=False, error=error or "no se pudo re-planificar", aborted="corrección",
                notes=[f"pasos pendientes descartados: {len(remaining)}"],
                duration_s=time.perf_counter() - started,
            )
        report = self.execute(actions)
        report.duration_s = time.perf_counter() - started
        return report

    def _allowed(self, action: Action) -> bool:
        """Aplica la política de confirmación (``off``/``danger``/``auto``)."""
        if not action.destructive or self.confirm_mode == "auto":
            return True
        if self.confirm is not None:
            try:
                return bool(self.confirm(action))
            except Exception as exc:
                self.log.debug("confirmación falló: %s", exc)
                return self.confirm_mode == "auto"
        # sin interlocutor que confirme: en "off" se pregunta, aquí se omite
        return self.confirm_mode != "off"

    def _apply_glow(self, mode: str) -> None:
        glow = self.glow
        if glow is None:
            return
        try:
            if mode == "off":
                glow.end()
            elif hasattr(glow, "pulse"):
                glow.pulse(mode, duration_ms=900)
        except Exception as exc:  # el glow nunca puede romper una acción
            self.log.debug("glow ignorado: %s", exc)

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_state is not None:
            try:
                self.on_state(name, payload)
            except Exception:
                pass
        if self.bus is not None:
            try:
                self.bus.publish(f"eon.action.{name}", **payload)
            except Exception:
                pass
