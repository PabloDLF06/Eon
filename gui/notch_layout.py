"""Máquina de estados y geometría del Dynamic Notch (spec 2.1).

Aquí vive la "física" de la isla: los tamaños canónicos de cada estado, el
muelle de la transición (OutBack al abrir, OutCubic al cerrar), las barras de
audio reactivas, el anillo orbital de carga de modelo y la tarjeta de media.

:``NotchController`` no sabe nada de Qt: el widget le pasa ``dt`` y le pregunta
por el snapshot del fotograma. Eso permite:

* animar la ventana real con ``QPropertyAnimation`` y, a la vez, dibujar el
  contenido interpolado con el mismo ``progress`` (nada se desincroniza);
* reproducir en tests una secuencia completa de estados sin abrir ventanas;
* renderizar la previsualización fuera de línea con Pillow.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum

from gui import scene
from gui.char_kinematics import CharFrame, CharModel, CharState, CharTuningData
from gui.char_kinematics import build_scene as build_char_scene
from gui.scene import Paint, Scene, Shape, Stroke, TextShape


class NotchState(str, Enum):
    """Estados cinéticos de la cápsula."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    MEDIA = "media"
    ACTION = "action"
    ERROR = "error"
    SLEEPING = "sleeping"

    @classmethod
    def parse(cls, value: NotchState | str) -> NotchState:
        if isinstance(value, NotchState):  # str(enum) en 3.11 da "NotchState.X": nunca pasar por ahí
            return value
        key = str(value or "").strip().lower()
        aliases = {
            "": "idle",
            "normal": "idle",
            "processing": "thinking",
            "swapping": "thinking",
            "loading": "thinking",
            "recording": "listening",
            "wake": "listening",
            "music": "media",
            "player": "media",
            "automation": "action",
            "sleep": "sleeping",
            "muted": "sleeping",
            "dnd": "sleeping",
            "alert": "error",
        }
        name = aliases.get(key, key)
        try:
            return cls(name)
        except ValueError:
            return cls.IDLE


#: Estados que "gritan": deben interrumpir una animación en curso.
PRIORITY_STATES: tuple[NotchState, ...] = (NotchState.ERROR, NotchState.LISTENING)


@dataclass(frozen=True)
class MediaInfo:
    """Metadatos que muestra la Media / Action Card."""

    title: str = ""
    artist: str = ""
    position_s: float = 0.0
    duration_s: float = 0.0
    source: str = ""            # "Spotify" | "Comet / YouTube" | ...
    playing: bool = False

    @property
    def label(self) -> str:
        if self.title and self.artist:
            return f"{self.artist} — {self.title}"
        return self.title or self.artist or "Sin reproducción"

    @property
    def progress(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return scene.clamp01(self.position_s / self.duration_s)

    def formatted(self) -> str:
        """``0:42 / 3:39`` como en un reproductor normal."""
        def clock(seconds: float) -> str:
            seconds = max(0, int(seconds))
            return f"{seconds // 60}:{seconds % 60:02d}"

        if self.duration_s <= 0:
            return clock(self.position_s)
        return f"{clock(self.position_s)} / {clock(self.duration_s)}"


@dataclass(frozen=True)
class Geometry:
    """Ancho/alto/radio de la cápsula en un instante dado."""

    width: float
    height: float
    corner: float
    band: float = 150.0

    def corner_ratio(self) -> float:
        return scene.clamp01(self.corner / max(1.0, self.height))


def corner_params() -> tuple[float, float, float]:
    """(ratio, mínimo, máximo) de radio inferior, leídos de ``config``."""
    try:
        from config import NOTCH_CORNER_RATIO, NOTCH_MAX_CORNER, NOTCH_MIN_CORNER

        return (float(NOTCH_CORNER_RATIO), float(NOTCH_MIN_CORNER), float(NOTCH_MAX_CORNER))
    except Exception:  # pragma: no cover - config siempre importable
        return (0.66, 12.0, 28.0)


def capsule_corner(
    width: float,
    height: float,
    ratio: float | None = None,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    """Radio de las esquinas inferiores: crece con la altura, topa en ``hi``.

    La referencia visual ancla la barra al bisel con las esquinas superiores a
    radio 0; el redondeo fuerte sólo aparece abajo, que es lo que le da el
    aspecto de "cápsula derretida". Por eso el radio depende de la *altura*.
    """
    default_ratio, default_lo, default_hi = corner_params()
    ratio = default_ratio if ratio is None else ratio
    lo = default_lo if lo is None else lo
    hi = default_hi if hi is None else hi
    base = min(height * ratio, width * 0.45)
    return float(scene.clamp(base, lo, hi))


def morph_geometry(
    from_size: tuple[float, float],
    to_size: tuple[float, float],
    progress: float,
    ratio: float | None = None,
    lo: float | None = None,
    hi: float | None = None,
) -> Geometry:
    """Interpola dos tamaños de estado y calcula el radio resultante."""
    t = scene.clamp01(progress)
    width = scene.lerp(from_size[0], to_size[0], t)
    height = scene.lerp(from_size[1], to_size[1], t)
    return Geometry(width=width, height=height, corner=capsule_corner(width, height, ratio, lo, hi))


def soundbar_levels(count: int, phase: float, drive: float, seed: float = 0.0) -> list[float]:
    """Alturas 0..1 de las barritas de escucha (cian eléctrico).

    Combina ruido determinista con un perfil en campana: las barras centrales
    responden más, como un analizador de espectro visto de frente.
    """
    n = max(3, int(count))
    noise = scene.noise_series(n, phase * 2.4, octaves=3, seed=seed)
    levels: list[float] = []
    for i, value in enumerate(noise):
        center = (i - (n - 1) / 2.0) / max(1.0, (n - 1) / 2.0)
        bell = 1.0 - 0.62 * center * center
        wave = 0.5 + 0.5 * math.sin(phase * 6.0 + i * 0.55)
        raw = (0.35 * wave + 0.65 * (0.5 + 0.5 * value)) * bell
        # suelo de 0.10: las barras nunca se apagan del todo, la isla "vive"
        levels.append(scene.clamp(scene.lerp(0.10, 1.0, raw) * drive, 0.06, 1.0))
    return levels


def equalizer_levels(count: int, phase: float, playing: bool, decay: list[float] | None = None) -> list[float]:
    """Ecualizador de la media card: sube rápido, cae lento (mantén los picos)."""
    n = max(6, int(count))
    noise = scene.noise_series(n, phase * 1.35, octaves=4, seed=3.0)
    peaks = list(decay) if decay and len(decay) == n else [0.0] * n
    out: list[float] = []
    for i, value in enumerate(noise):
        target = (0.5 + 0.5 * value) if playing else 0.05
        target *= 1.0 - 0.35 * abs(i - n / 2.0) / max(1.0, n / 2.0)
        peaks[i] = max(peaks[i] * 0.94, target)
        out.append(scene.clamp(peaks[i], 0.03, 1.0))
    return out


@dataclass(frozen=True)
class NotchSnapshot:
    """Fotograma completo que el widget pinta y el test comprueba."""

    state: NotchState
    geometry: Geometry
    progress: float
    morphing: bool
    hovered: bool
    interactive: bool
    glow_alpha: float
    char: CharFrame
    bars: tuple[float, ...] = ()
    bars_kind: str = "soundwave"
    ring_angle: float = 0.0
    ring_alpha: float = 0.0
    status: str = ""
    detail: str = ""
    media: MediaInfo = field(default_factory=MediaInfo)
    accent: str = "#00e5ff"
    error: bool = False


@dataclass
class NotchController:
    """Controlador puro del notch. Un hilo (el de GUI) lo hace avanzar.

    Parameters
    ----------
    sizes:
        Mapa ``estado -> (ancho, alto)`` (``config.NOTCH_SIZES``).
    char:
        Modelo del personaje; se crea uno si no se pasa ninguno.
    auto_collapse_s:
        Segundos de inactividad antes de volver al reposo.
    """

    sizes: dict[str, tuple[int, int]] = field(default_factory=dict)
    char: CharModel = field(default_factory=CharModel)
    anim_ms: dict[str, int] = field(default_factory=dict)
    auto_collapse_s: float = 6.0
    error_timeout_s: float = 7.0
    media_sticky: bool = True

    def __post_init__(self) -> None:
        if not self.sizes:
            from config import NOTCH_SIZES  # import local: evita ciclos y arrastra GUI

            self.sizes = dict(NOTCH_SIZES)
        if not self.anim_ms:
            from config import NOTCH_ANIM_MS

            self.anim_ms = dict(NOTCH_ANIM_MS)
        self.state: NotchState = NotchState.IDLE
        self.previous: NotchState = NotchState.IDLE
        self.progress: float = 1.0
        self.elapsed: float = 0.0
        self.duration: float = 0.0
        self.hovered: bool = False
        self.status: str = ""
        self.detail: str = ""
        self.accent: str = "#00e5ff"
        self.media: MediaInfo = MediaInfo()
        self.last_activity: float = time.monotonic()
        self._error_until: float = 0.0
        self._peaks: list[float] = []
        self._phase: float = 0.0
        self._pending: NotchState | None = None

    # ------------------------------------------------------------------ API --
    def size_of(self, state: NotchState) -> tuple[int, int]:
        """Tamaño canónico del estado, con un valor por defecto razonable."""
        return tuple(self.sizes.get(state.value, (180, 44)))  # type: ignore[return-value]

    def set_state(self, state: NotchState | str, status: str = "", detail: str = "", accent: str | None = None) -> None:
        """Solicita un estado nuevo y arranca la transición morfológica."""
        new_state = NotchState.parse(state)
        if new_state is self.state and not self.morphing:
            self.note_activity()
            if status:
                self.status = status
            if detail:
                self.detail = detail
            return
        self.previous = self.state
        self.state = new_state
        self.progress = 0.0
        self.elapsed = 0.0
        expand = self.size_of(new_state)[1] >= self.size_of(self.previous)[1]
        self.duration = max(0.05, (self.anim_ms.get("expand" if expand else "collapse", 340)) / 1000.0)
        self.accent = accent or default_accent(new_state)
        self._pending = None
        if new_state is NotchState.ERROR:
            self._error_until = time.monotonic() + self.error_timeout_s
        if new_state in (NotchState.MEDIA, NotchState.ACTION):
            self.media = replace(self.media)
        self.char.set_state(char_state_for(new_state))
        self.note_activity()

    def note_activity(self) -> None:
        self.last_activity = time.monotonic()

    def set_status(self, status: str, detail: str = "") -> None:
        """Línea de texto del notch (transcripción, plan en curso, modelo...)."""
        self.status = status
        if detail:
            self.detail = detail
        self.note_activity()

    def set_media(self, media: MediaInfo) -> None:
        self.media = media
        if media.playing and self.state not in (NotchState.MEDIA, NotchState.ACTION):
            self.set_state(NotchState.MEDIA)
        elif not media.playing and self.state is NotchState.MEDIA:
            self.set_state(NotchState.IDLE)
        self.note_activity()

    def hover(self, hovered: bool) -> None:
        """El hover desactiva el click-through y prepara la zona interactiva."""
        if hovered != self.hovered:
            self.hovered = hovered
            self.note_activity()

    def collapse(self) -> None:
        """Vuelta forzada al reposo (lo invoca el kill switch, entre otros)."""
        self.media = replace(self.media, playing=False)
        self.status = ""
        self.detail = ""
        self._pending = None
        self.set_state(NotchState.IDLE, accent="#00e5ff")

    @property
    def morphing(self) -> bool:
        return self.progress < 1.0

    @property
    def interactive(self) -> bool:
        """True cuando la cápsula debe aceptar ratón/teclado."""
        return self.hovered or self.state in (NotchState.MEDIA, NotchState.ACTION, NotchState.ERROR)

    def tick(self, dt: float) -> NotchSnapshot:
        """Avanza animaciones y devuelve el fotograma a pintar."""
        dt = scene.clamp(float(dt), 0.0, 0.2)
        self._phase += dt
        if self.morphing:
            self.elapsed += dt
            self.progress = scene.clamp01(self.elapsed / self.duration)
            if self.progress >= 1.0:
                self.progress = 1.0
        if self.state is NotchState.ERROR and self._error_until and time.monotonic() >= self._error_until:
            self.set_state(NotchState.IDLE)
        idle_for = time.monotonic() - self.last_activity
        in_card = self.state in (NotchState.MEDIA, NotchState.ACTION)
        # la media card es pegajosa mientras suena (o si el usuario la fija);
        # cualquier otro estado (escucha, pensando, error) vuelve al reposo solo
        media_live = in_card and (self.media.playing or self.media_sticky)
        if self.state not in (NotchState.IDLE, NotchState.SLEEPING) and not media_live and idle_for > self.auto_collapse_s:
            self.set_state(NotchState.IDLE)
        if self.state is NotchState.MEDIA and self.media.playing:
            self.media = replace(self.media, position_s=self.media.position_s + dt)

        char_frame = self.char.update(dt, char_state_for(self.state))
        return self.snapshot(char_frame)

    # ------------------------------------------------------------- snapshot --
    def snapshot(self, char_frame: CharFrame | None = None) -> NotchSnapshot:
        """Fotograma actual sin avanzar relojes (para tests y repintados)."""
        eased = ease_for(self.state, self.previous, self.anim_ms)(self.progress)
        geometry = morph_geometry(
            self.size_of(self.previous),
            self.size_of(self.state),
            eased,
        )
        frame = char_frame or self.char.frame
        bars: tuple[float, ...] = ()
        kind = "none"
        if self.state is NotchState.LISTENING:
            from config import SOUNDBAR_COUNT

            drive = 0.55 + 0.45 * min(1.0, frame.mouth_open * 2.0 + frame.halo_alpha)
            bars = tuple(soundbar_levels(SOUNDBAR_COUNT, self._phase, drive))
            kind = "soundwave"
        elif self.state in (NotchState.MEDIA, NotchState.ACTION):
            from config import EQUALIZER_BARS

            bars = tuple(equalizer_levels(EQUALIZER_BARS, self._phase, self.media.playing or self.state is NotchState.ACTION, self._peaks))
            self._peaks = list(bars)
            kind = "equalizer"
        elif self.state is NotchState.THINKING:
            from config import SOUNDBAR_COUNT

            bars = tuple(soundbar_levels(max(7, SOUNDBAR_COUNT // 2), self._phase * 0.35, 0.18, seed=9.0))
            kind = "ambient"

        breathe = 0.5 + 0.5 * math.sin(2.0 * math.pi * self._phase / 3.0)
        if self.state is NotchState.IDLE:
            glow = scene.lerp(26.0, 78.0, breathe) / 255.0
        elif self.state is NotchState.SLEEPING:
            glow = 0.06 + 0.04 * breathe
        else:
            glow = 0.55 + 0.25 * breathe

        ring_alpha = 1.0 if self.state is NotchState.THINKING else 0.0
        return NotchSnapshot(
            state=self.state,
            geometry=geometry,
            progress=eased,
            morphing=self.morphing,
            hovered=self.hovered,
            interactive=self.interactive,
            glow_alpha=glow,
            char=frame,
            bars=bars,
            bars_kind=kind,
            ring_angle=(self._phase * 260.0) % 360.0,
            ring_alpha=ring_alpha,
            status=self.status,
            detail=self.detail,
            media=self.media,
            accent=self.accent if self.state is not NotchState.IDLE else "#00e5ff",
            error=self.state is NotchState.ERROR,
        )


# --------------------------------------------------------------------------- #
# Utilidades de estado
# --------------------------------------------------------------------------- #

def default_accent(state: NotchState) -> str:
    """Color de acento por estado, según la spec."""
    return {
        NotchState.IDLE: "#00e5ff",
        NotchState.LISTENING: "#00e5ff",
        NotchState.THINKING: "#a78bfa",
        NotchState.MEDIA: "#ffc46b",
        NotchState.ACTION: "#00f0ff",
        NotchState.ERROR: "#ff6b8a",
        NotchState.SLEEPING: "#7f8bd6",
    }.get(state, "#00e5ff")


def char_state_for(state: NotchState) -> CharState:
    """Empareja el estado del notch con la expresión del personaje."""
    return {
        NotchState.IDLE: CharState.IDLE,
        NotchState.LISTENING: CharState.LISTENING,
        NotchState.THINKING: CharState.THINKING,
        NotchState.MEDIA: CharState.SPEAKING,
        NotchState.ACTION: CharState.VISION,
        NotchState.ERROR: CharState.ERROR,
        NotchState.SLEEPING: CharState.SLEEPING,
    }.get(state, CharState.IDLE)


def ease_for(state: NotchState, previous: NotchState, anim_ms: dict[str, int] | None = None) -> Callable[[float], float]:
    """OutBack al crecer, OutCubic al encogerse (spec 2.1)."""
    growing = state.value in ("media", "action", "listening", "thinking", "error") and previous.value in (
        "idle",
        "sleeping",
        "listening",
    )
    if growing or state in (NotchState.MEDIA, NotchState.ACTION, NotchState.LISTENING):
        return scene.out_back
    return scene.out_cubic


# --------------------------------------------------------------------------- #
# Escena vectorial del notch
# --------------------------------------------------------------------------- #

def build_scene(
    snapshot: NotchSnapshot,
    char_tuning: CharTuningData | None = None,
    screen_width: float = 1920.0,
) -> Scene:
    """Pinta la cápsula completa (cuerpo, brillo, barras, anillo, textos).

    El sistema de coordenadas es el de la *ventana* del notch: ancho ``band``
    horizontal centrado en la pantalla y alto ``NOTCH_BAND_HEIGHT``, con la
    cápsula colgando del borde superior (y=0).
    """
    geo = snapshot.geometry
    from config import NOTCH_BAND_HEIGHT

    band = NOTCH_BAND_HEIGHT
    w = band_width(screen_width)
    cx = w / 2.0
    left = cx - geo.width / 2.0
    top = 0.0
    shapes: list[Shape] = []

    # 1. Resplandor ambiental: halo ancho y suave bajo la cápsula.
    halo_pts = scene.capsule_points(cx, -geo.height * 0.18, geo.width * 1.30, geo.height * 1.75, geo.corner * 1.6, samples=26)
    shapes.append(
        Shape(
            points=tuple(halo_pts),
            fill=Paint(color=snapshot.accent, alpha=snapshot.glow_alpha * 0.16, radial=True),
            closed=True,
            tag="halo",
            z=-40,
        )
    )

    # 2. Cáscara negra, pegada al bisel, con las esquinas inferiores redondas.
    shell_pts = scene.capsule_points(cx, top, geo.width, geo.height, geo.corner, samples=30)
    shapes.append(
        Shape(
            points=tuple(shell_pts),
            fill=Paint(color="#000000", alpha=0.985),
            closed=True,
            tag="shell",
            z=-30,
        )
    )
    shapes.append(
        Shape(
            points=tuple(shell_pts),
            stroke=Stroke(color="#2a2b3a", width=1.0, alpha=0.55 if not snapshot.interactive else 0.85),
            closed=True,
            tag="rim",
            z=-29,
        )
    )
    if snapshot.interactive:
        shapes.append(
            Shape(
                points=tuple(shell_pts),
                stroke=Stroke(color=snapshot.accent, width=1.4, alpha=0.55, glow=5.0),
                closed=True,
                tag="rim-active",
                z=-28,
            )
        )

    # 3. Interior de la cápsula.
    inner = (left + 8.0, top + 4.0, geo.width - 16.0, geo.height - 8.0)
    char_box = char_box_for(snapshot, inner)
    shapes.extend(build_char_scene(snapshot.char, char_box, char_tuning).shapes)

    texts: list[TextShape] = []
    if snapshot.state is NotchState.LISTENING:
        shapes.extend(_soundwave(snapshot, inner, geo))
        texts = [_listening_text(snapshot, inner, char_box)]
    elif snapshot.state in (NotchState.MEDIA, NotchState.ACTION):
        shapes.extend(_media_card(snapshot, inner, geo))
        texts = _media_text(snapshot, inner, geo, char_box)
    elif snapshot.state is NotchState.THINKING:
        shapes.extend(_orbital(snapshot, inner, geo))
        texts = _thinking_text(snapshot, inner, geo, char_box)
    elif snapshot.state is NotchState.ERROR:
        shapes.extend(_error_card(snapshot, inner, geo))
        texts = _error_text(snapshot, inner, geo, char_box)
    elif snapshot.state is NotchState.SLEEPING:
        shapes.append(
            Shape(
                points=(),
                fill=None,
                tag="sleep",
                z=20,
            )
        )

    return Scene(
        width=w,
        height=band,
        shapes=tuple([s for s in shapes if s.points] + [t for t in texts if t.text]),
        clip=tuple(shell_pts),
        meta={
            "state": snapshot.state.value,
            "width": round(geo.width, 2),
            "height": round(geo.height, 2),
            "corner": round(geo.corner, 2),
        },
    )


def band_width(screen_width: float) -> float:
    """Ancho de la franja: el doble de la pantalla no hace falta, pero el
    widget de Qt usa la geometría real; esta función existe para que la
    previsualización y los tests usen el mismo criterio."""
    return float(max(720.0, screen_width))


def char_box_for(snapshot: NotchSnapshot, inner: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Cuánto sitio ocupa el personaje según el estado.

    En reposo el personaje es diminuto (asoma, como en la referencia); al
    escuchar crece y a la izquierda deja hueco para las barras; en la media
    card se queda en un extremo y el resto es para el dashboard.
    """
    x, y, w, h = inner
    state = snapshot.state
    if state is NotchState.IDLE:
        side = min(h * 0.98, w * 0.36)
        return (x + w / 2.0 - side / 2.0, y + (h - side) / 2.0 - side * 0.02, side, side)
    if state is NotchState.SLEEPING:
        side = min(h * 0.98, w * 0.42)
        return (x + w / 2.0 - side / 2.0, y + (h - side) / 2.0, side, side)
    if state in (NotchState.MEDIA, NotchState.ACTION):
        side = min(h * 0.86, w * 0.22)
        return (x + w * 0.015, y + (h - side) / 2.0, side, side)
    if state is NotchState.THINKING:
        side = min(h * 0.92, w * 0.30)
        return (x + w * 0.04, y + (h - side) / 2.0, side, side)
    side = min(h * 0.96, w * 0.30)
    return (x + w * 0.03, y + (h - side) / 2.0, side, side)


def _soundwave(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry) -> list[Shape]:
    """Barras cian reactivas a la escucha (spec: ``#00e5ff``)."""
    x, y, w, h = inner
    bars = list(snapshot.bars) or [0.2] * 9
    count = len(bars)
    char_w = h * 1.25
    area_x = x + char_w + 10.0
    area_w = max(20.0, x + w - area_x)
    bar_w = max(2.0, area_w / (count * 1.75))
    gap = bar_w * 0.75
    total = count * bar_w + (count - 1) * gap
    start = area_x + max(0.0, (area_w - total) / 2.0)
    mid_y = y + h / 2.0
    out: list[Shape] = []
    for i, level in enumerate(bars):
        bar_h = max(2.0, (h * 0.82) * max(0.08, level))
        bx = start + i * (bar_w + gap)
        out.append(
            Shape(
                points=tuple(scene.rounded_rect_points(bx, mid_y - bar_h / 2.0, bar_w, bar_h, bar_w / 2.0, bar_w / 2.0, samples=6)),
                fill=Paint(color="#00e5ff", alpha=0.55 + 0.45 * level),
                stroke=Stroke(color="#00e5ff", width=0.8, alpha=0.35, glow=2.2),
                closed=True,
                tag="bar",
                z=30 + i,
            )
        )
    return out


def _orbital(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry) -> list[Shape]:
    """Anillo neón orbital de "cargando modelo" con puntos de avance."""
    x, y, w, h = inner
    out: list[Shape] = []
    ring_cx = x + w - h * 0.62
    ring_cy = y + h / 2.0
    radius = h * 0.34
    start = snapshot.ring_angle
    # arco principal
    pts = scene.ellipse_points(ring_cx, ring_cy, radius, radius * 0.92, samples=64, start_deg=start, span_deg=250.0)
    out.append(
        Shape(
            points=tuple(pts),
            stroke=Stroke(color="#a78bfa", width=2.2, alpha=0.95, glow=4.0),
            closed=False,
            tag="ring",
            z=31,
        )
    )
    pts2 = scene.ellipse_points(ring_cx, ring_cy, radius * 0.62, radius * 0.58, samples=40, start_deg=-start * 0.7, span_deg=150.0)
    out.append(
        Shape(
            points=tuple(pts2),
            stroke=Stroke(color="#00e5ff", width=1.5, alpha=0.8, glow=2.6),
            closed=False,
            tag="ring-inner",
            z=32,
        )
    )
    return out


def _media_card(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry) -> list[Shape]:
    """Dashboard de música/automatización: barras de ecualizador y progreso.

    El texto (pista, fuente, tiempo) lo aportan :func:`_media_text`; aquí sólo
    hay geometría para que la tarjeta siga siendo legible sin fuentes.
    """
    _x, y, _w, h = inner
    out: list[Shape] = []
    content_x, content_w = media_content_box(snapshot, inner, geo)
    bars = list(snapshot.bars) or [0.2] * 12
    count = len(bars)
    if count:
        slot = content_w / count
        bar_w = max(1.6, slot * 0.40)
        band_h = h * 0.22
        base_y = y + h * 0.60
        for i, level in enumerate(bars):
            bh = max(1.5, band_h * level)
            out.append(
                Shape(
                    points=tuple(
                        scene.rounded_rect_points(
                            content_x + i * slot, base_y + (band_h - bh) / 2.0, bar_w, bh, bar_w / 2.0, bar_w / 2.0, samples=4
                        )
                    ),
                    fill=Paint(color=snapshot.accent, alpha=0.45 + 0.5 * level),
                    closed=True,
                    tag="eq",
                    z=30 + i,
                )
            )
    # barra de progreso
    prog = snapshot.media.progress if snapshot.media.duration_s > 0 else (0.5 + 0.5 * math.sin(snapshot.char.time * 0.35))
    track_y = y + h * 0.86
    out.append(
        Shape(
            points=tuple(scene.rounded_rect_points(content_x, track_y, content_w, 2.2, 1.1, 1.1, samples=4)),
            fill=Paint(color="#ffffff", alpha=0.16),
            closed=True,
            tag="track",
            z=60,
        )
    )
    out.append(
        Shape(
            points=tuple(
                scene.rounded_rect_points(
                    content_x, track_y, max(2.0, content_w * scene.clamp01(prog)), 2.2, 1.1, 1.1, samples=4
                )
            ),
            fill=Paint(color=snapshot.accent, alpha=0.95),
            stroke=Stroke(color=snapshot.accent, width=0.8, alpha=0.6, glow=2.4),
            closed=True,
            tag="track-fill",
            z=61,
        )
    )
    return out


def media_content_box(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry) -> tuple[float, float]:
    """Caja de contenido a la derecha del personaje (evita solapar el blob)."""
    x, _y, w, _h = inner
    char_box = char_box_for(snapshot, inner)
    start = char_box[0] + char_box[2] + 12.0
    return start, max(24.0, x + w - start - 4.0)


def _media_text(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry, char_box: tuple[float, float, float, float]) -> list[TextShape]:
    """Dos líneas: la pista y la fuente con el tiempo."""
    _x, y, _w, h = inner
    tx, tw = media_content_box(snapshot, inner, geo)
    media = snapshot.media
    if snapshot.state is NotchState.ACTION:
        title = snapshot.status or "Automatizando el escritorio"
        meta = snapshot.detail or "vigilando la pantalla"
    else:
        title = media.label
        bits = [b for b in (media.source, media.formatted() if media.duration_s > 0 else "") if b]
        meta = " · ".join(bits) if bits else "en pausa"
    return [
        TextShape(x=tx, y=y + h * 0.24, text=_fit(title, tw, 21), size=13.0, color="#f4f7fb", alpha=0.98, bold=True, tag="title", z=70),
        TextShape(x=tx, y=y + h * 0.44, text=_fit(meta, tw, 26), size=9.5, color=snapshot.accent, alpha=0.85, tag="meta", z=70),
    ]


def _thinking_text(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry, char_box: tuple[float, float, float, float]) -> list[TextShape]:
    x, y, w, h = inner
    tx = char_box[0] + char_box[2] + 10.0
    tw = max(24.0, x + w - tx - h * 1.15)
    primary = snapshot.status or "Pensando"
    secondary = snapshot.detail or ""
    return [
        TextShape(x=tx, y=y + h * 0.36, text=_fit(primary, tw, 20), size=11.0, color="#e8ecf6", alpha=0.96, bold=True, tag="think", z=70),
        TextShape(x=tx, y=y + h * 0.66, text=_fit(secondary, tw, 22), size=9.0, color="#a78bfa", alpha=0.9, tag="think-detail", z=70),
    ]


def _listening_text(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], char_box: tuple[float, float, float, float]) -> TextShape:
    """Transcripción en vivo, en cian, debajo de las barras si hay sitio."""
    x, y, w, h = inner
    if not snapshot.status:
        return TextShape(x=0, y=0, text="", tag="listen-empty")
    return TextShape(
        x=x + w - 6.0,
        y=y + h * 0.82,
        text=_fit(snapshot.status, w * 0.62, 24),
        size=9.0,
        color="#9beeff",
        alpha=0.9,
        anchor="right",
        tag="listen-text",
        z=71,
    )


def _error_text(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry, char_box: tuple[float, float, float, float]) -> list[TextShape]:
    x, y, w, h = inner
    tx = char_box[0] + char_box[2] + 12.0
    tw = max(24.0, x + w - tx - 14.0)
    return [
        TextShape(x=tx, y=y + h * 0.36, text=_fit(snapshot.status or "Algo ha fallado", tw, 24), size=11.0, color="#ffd3dc", alpha=0.98, bold=True, tag="err", z=70),
        TextShape(x=tx, y=y + h * 0.66, text=_fit(snapshot.detail or "EON sigue a la escucha; Ctrl+Shift+Space libera el control", tw, 44), size=9.0, color="#ff9db1", alpha=0.9, tag="err-detail", z=70),
    ]


def _fit(text: str, width: float, size: float) -> str:
    """Recorta el texto a un ancho aproximado (0.52 * tamaño por carácter)."""
    if not text:
        return ""
    approx = max(4, int(width / (size * 0.52)))
    return text if len(text) <= approx else text[: max(1, approx - 1)].rstrip() + "…"


def _error_card(snapshot: NotchSnapshot, inner: tuple[float, float, float, float], geo: Geometry) -> list[Shape]:
    x, y, w, h = inner
    pulse = 0.55 + 0.45 * math.sin(snapshot.char.time * 6.0)
    return [
        Shape(
            points=tuple(scene.ellipse_points(x + w - h * 0.42, y + h * 0.30, h * 0.10, h * 0.10, samples=20)),
            fill=Paint(color="#ff6b8a", alpha=pulse),
            stroke=Stroke(color="#ff6b8a", width=1.0, alpha=pulse * 0.7, glow=3.0),
            closed=True,
            tag="error-led",
            z=40,
        )
    ]


def idle_geometry(width: float = 140.0, height: float = 32.0) -> Geometry:
    """Atajo de tests/previsualización: geometría de reposo."""
    return Geometry(width=width, height=height, corner=capsule_corner(width, height))
