"""Capa de escena independiente de Qt.

Tanto el widget de PyQt6 como la previsualización fuera de línea consumen esta
estructura. La idea es simple: la *cinemática* (cuánto se abre el ojo, dónde va
la boca, qué radio tiene la cápsula en mitad de una transición) vive aquí, en
Python puro, y los pintores sólo traducen primitivas a ``QPainter`` o a Pillow.

Ventajas concretas:

* Los estados del personaje y del notch se pueden comprobar con tests unitarios
  sin abrir una ventana ni depender de un monitor.
* Un mismo cambio de geometría se refleja a la vez en pantalla y en el render
  de previsualización, así que no pueden divergir.
* Las primitivas son deliberadamente pocas y todas muestreadas: las curvas son
  polígonos densos, lo que hace trivial el pintado y el recorte.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Color
# --------------------------------------------------------------------------- #

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Convierte ``#rgb`` / ``#rrggbb`` en ``(r, g, b)``."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"color hexadecimal inválido: {value!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:  # pragma: no cover - defensivo
        raise ValueError(f"color hexadecimal inválido: {value!r}") from exc


def rgb_to_hex(rgb: Sequence[int]) -> str:
    """Lo inverso a :func:`hex_to_rgb`, con saturación de canales."""
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def mix(color_a: str, color_b: str, weight: float) -> str:
    """Interpolación lineal entre dos colores. ``weight=0`` devuelve A."""
    w = clamp01(weight)
    ca, cb = hex_to_rgb(color_a), hex_to_rgb(color_b)
    return rgb_to_hex([ca[i] + (cb[i] - ca[i]) * w for i in range(3)])


def with_alpha(color: str, alpha: float) -> tuple[int, int, int, int]:
    """Descompone un color en ``(r, g, b, a)`` para renderizadores que lo piden."""
    r, g, b = hex_to_rgb(color)
    return (r, g, b, int(round(clamp01(alpha) * 255)))


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else float(value)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * clamp01(t)


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    """Interpolación suave de Hermite; evita cortes duros en las transiciones."""
    if edge1 == edge0:
        return 1.0 if value >= edge1 else 0.0
    t = clamp01((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------- #
# Easing (spec 2.1: OutBack y OutCubic)
# --------------------------------------------------------------------------- #

def out_cubic(t: float) -> float:
    """QEasingCurve.Type.OutCubic equivalente: desacelera hasta parar."""
    x = 1.0 - clamp01(t)
    return 1.0 - x * x * x


def out_back(t: float, overshoot: float = 1.70158) -> float:
    """QEasingCurve.Type.OutBack equivalente: pequeño rebote elástico."""
    x = clamp01(t) - 1.0
    c3 = overshoot + 1.0
    return 1.0 + c3 * x**3 + overshoot * x * x


def out_elastic(t: float, periods: float = 2.4) -> float:
    """Rebote amortiguado; usado en la oreja/antena al erguirse."""
    x = clamp01(t)
    if x in (0.0, 1.0):
        return x
    return math.pow(2.0, -10.0 * x) * math.sin((x - 0.075) * (math.pi * 2.0 * periods)) + 1.0


def ease_in_out_sine(t: float) -> float:
    return -(math.cos(math.pi * clamp01(t)) - 1.0) / 2.0


# --------------------------------------------------------------------------- #
# Generadores de geometría
# --------------------------------------------------------------------------- #

def squircle_points(
    cx: float,
    cy: float,
    half_w: float,
    half_h: float,
    exponent: float = 3.2,
    samples: int = 96,
    bottom_bias: float = 1.0,
) -> list[tuple[float, float]]:
    """Superelipse de Lamé: la silueta "morbida" del personaje.

    ``exponent=2`` es una elipse perfecta; valores 3-4 dan la cápsula
    redondeada tipo *squircle*. ``bottom_bias`` estira el radio inferior para
    que la base se vea más "sentada" que la coronilla, igual que la referencia.
    """
    points: list[tuple[float, float]] = []
    n = max(16, int(samples))
    e = max(2.0, float(exponent))
    for i in range(n):
        theta = 2.0 * math.pi * i / n
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        rx = half_w * _pow_unit(cos_t, 2.0 / e)
        bias = bottom_bias if sin_t > 0 else 1.0
        ry = half_h * _pow_unit(sin_t, 2.0 / e) * bias
        points.append((cx + rx, cy + ry))
    return points


def _pow_unit(value: float, power: float) -> float:
    """``sign(v) * |v| ** power`` estable para potencias fraccionarias."""
    return math.copysign(abs(value) ** power, value)


def capsule_points(
    cx: float,
    top: float,
    width: float,
    height: float,
    corner: float,
    samples: int = 40,
) -> list[tuple[float, float]]:
    """Contorno de la cápsula del notch: arriba recto al bisel, abajo redondeado.

    Es la firma visual del Dynamic Island: las esquinas superiores van a radio
    0 (o casi) porque continúan el borde físico de la pantalla, y las
    inferiores llevan el radio grande que "derrite" la barra hacia dentro.
    """
    radius = max(0.5, min(corner, height / 2.0, width / 2.0))
    left = cx - width / 2.0
    right = cx + width / 2.0
    bottom = top + height
    per_corner = max(4, int(samples) // 2)
    pts: list[tuple[float, float]] = [(left, top), (right, top)]
    # cuadrante inferior-derecho: de la vertical (0°) a la horizontal (90°)
    rcx, rcy = right - radius, bottom - radius
    for i in range(per_corner + 1):
        ang = (math.pi / 2.0) * (i / per_corner)
        pts.append((rcx + math.cos(ang) * radius, rcy + math.sin(ang) * radius))
    # cuadrante inferior-izquierdo: de la horizontal (90°) a la vertical (180°)
    lcx, lcy = left + radius, bottom - radius
    for i in range(1, per_corner + 1):
        ang = math.pi / 2.0 + (math.pi / 2.0) * (i / per_corner)
        pts.append((lcx + math.cos(ang) * radius, lcy + math.sin(ang) * radius))
    return pts


def rounded_rect_points(
    x: float,
    y: float,
    width: float,
    height: float,
    top_radius: float,
    bottom_radius: float,
    samples: int = 12,
) -> list[tuple[float, float]]:
    """Rectángulo con radios superior e inferior distintos, muestreado.

    Se usa para la tarjeta de música, las barras de audio y cualquier píldora
    de texto: un solo generador sirve a Qt y a la previsualización.
    """
    if width <= 0.0 or height <= 0.0:
        return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    rt = max(0.0, min(top_radius, height / 2.0, width / 2.0))
    rb = max(0.0, min(bottom_radius, height / 2.0, width / 2.0))
    pts: list[tuple[float, float]] = []
    n = max(2, int(samples))
    # esquina superior izquierda -> superior derecha
    for i in range(n + 1):
        ang = math.pi + (math.pi / 2.0) * (i / n)
        pts.append(_arc(x + rt, y + rt, rt, ang))
    for i in range(n + 1):
        ang = -math.pi / 2.0 + (math.pi / 2.0) * (i / n)
        pts.append(_arc(x + width - rt, y + rt, rt, ang))
    # inferior derecha -> inferior izquierda
    for i in range(n + 1):
        ang = (math.pi / 2.0) * (i / n)
        pts.append(_arc(x + width - rb, y + height - rb, rb, ang))
    for i in range(n + 1):
        ang = math.pi / 2.0 + (math.pi / 2.0) * (i / n)
        pts.append(_arc(x + rb, y + height - rb, rb, ang))
    return pts


def _arc(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    if radius <= 0.0:
        return (cx, cy)
    return (cx + math.cos(angle) * radius, cy + math.sin(angle) * radius)


def bezier_points(
    start: tuple[float, float],
    control: tuple[float, float],
    end: tuple[float, float],
    samples: int = 24,
) -> list[tuple[float, float]]:
    """Curva cuadrática de Bézier muestreada (trayectorias de ratón y sonrisas)."""
    n = max(2, int(samples))
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        it = 1.0 - t
        pts.append(
            (
                it * it * start[0] + 2 * it * t * control[0] + t * t * end[0],
                it * it * start[1] + 2 * it * t * control[1] + t * t * end[1],
            )
        )
    return pts


def ellipse_points(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    samples: int = 32,
    start_deg: float = 0.0,
    span_deg: float = 360.0,
) -> list[tuple[float, float]]:
    """Arco o elipse completa muestreada; sirve para ojos cerrados y anillos."""
    n = max(4, int(samples))
    span = math.radians(span_deg)
    base = math.radians(start_deg)
    if abs(abs(span_deg) - 360.0) < 1e-6:
        n += 1
    return [
        (cx + rx * math.cos(base + span * (i / (n - 1))), cy + ry * math.sin(base + span * (i / (n - 1))))
        for i in range(n)
    ]


def noise_series(
    count: int,
    phase: float,
    octaves: int = 3,
    seed: float = 1.0,
) -> list[float]:
    """Ruido determinista sumando senoides; alimenta barras de audio y temblor.

    Determinista a propósito: los tests y el render de previsualización deben
    poder reproducir el mismo fotograma.
    """
    out: list[float] = []
    for i in range(count):
        value = 0.0
        amplitude = 1.0
        total = 0.0
        for o in range(max(1, octaves)):
            freq = (o + 1) * 1.7
            value += amplitude * math.sin(phase * freq + (i + seed) * (0.7 + o * 0.37))
            total += amplitude
            amplitude *= 0.55
        out.append(value / max(1e-6, total))
    return out


# --------------------------------------------------------------------------- #
# Primitivas de la escena
# --------------------------------------------------------------------------- #

#: Umbral de ``z`` a partir del cual las formas se recortan por ``Scene.clip``.
#: Lo de debajo (``z`` más pequeño) es el aura: halo, sombra y cáscara, que por
#: definición desbordan la cápsula. Lo de encima es contenido y debe respetarla.
CLIP_START_Z: int = -25


@dataclass(frozen=True)
class Paint:
    """Relleno liso, con degradado lineal o radial."""

    color: str = "#ffffff"
    alpha: float = 1.0
    to_color: str | None = None      # degradado lineal color -> to_color
    radial: bool = False             # degradado radial desde el centro de la forma
    focal: tuple[float, float] | None = None  # punto de mayor brillo del radial

    def rgba(self) -> tuple[int, int, int, int]:
        return with_alpha(self.color, self.alpha)

    @property
    def is_gradient(self) -> bool:
        return self.to_color is not None or self.radial


@dataclass(frozen=True)
class Stroke:
    """Trazo con borde redondeado y atenuación opcional para simular neón."""

    color: str = "#ffffff"
    width: float = 1.0
    alpha: float = 1.0
    glow: float = 0.0                # radio extra de halo (0 = sin halo)
    passes: int = 0                  # pasadas del halo; 0 => auto según glow

    def rgba(self) -> tuple[int, int, int, int]:
        return with_alpha(self.color, self.alpha)

    def auto_passes(self) -> int:
        if self.glow <= 0.0:
            return 0
        return self.passes or max(2, int(min(6, self.glow / 2.0 + 1.5)))


@dataclass(frozen=True)
class Shape:
    """Cualquier cosa dibujable: relleno y/o trazo sobre una lista de puntos."""

    points: tuple[tuple[float, float], ...]
    fill: Paint | None = None
    stroke: Stroke | None = None
    closed: bool = True
    tag: str = ""
    #: Peso de render: mayor = más arriba en la pila. Orden estable secundario.
    z: int = 0


@dataclass(frozen=True)
class TextShape:
    """Texto con anclaje explícito para no depender del estado del pintor."""

    x: float
    y: float
    text: str
    size: float = 12.0
    color: str = "#ffffff"
    alpha: float = 1.0
    anchor: str = "left"     # left|center|right
    baseline: str = "middle"  # top|middle|bottom
    bold: bool = False
    family: str = ""
    tag: str = ""
    z: int = 5


@dataclass(frozen=True)
class Scene:
    """Lienzo: tamaño, recorte y primitivas ya ordenadas."""

    width: float
    height: float
    shapes: tuple[Shape | TextShape, ...] = ()
    #: Región de recorte (la cápsula): todo lo que salga fuera se desprecia.
    clip: tuple[tuple[float, float], ...] | None = None
    meta: dict[str, float | str | bool] = field(default_factory=dict)

    def with_shapes(self, extra: Iterable[Shape | TextShape]) -> Scene:
        return Scene(
            width=self.width,
            height=self.height,
            shapes=tuple(self.shapes) + tuple(extra),
            clip=self.clip,
            meta=dict(self.meta),
        )

    def sorted_shapes(self) -> list[Shape | TextShape]:
        """Orden de pintado: por ``z`` y, a igualdad, por inserción."""
        return sorted(self.shapes, key=lambda item: item.z)  # sort es estable


def glow_strokes(base: Stroke) -> list[Stroke]:
    """Expande un trazo en N pasadas decrecientes: halo de neón sin desenfoque.

    ``QPainter`` no tiene desenfoque gaussiano por trazo y un
    ``QGraphicsBlurEffect`` a 60 fps costaría demasiado; apilar trazos con
    alfa cayendo y grosor subiendo da el mismo aspecto por una fracción del
    coste. El renderizador Pillow de la previsualización hace lo mismo.
    """
    passes = base.auto_passes()
    if passes <= 0:
        return [base]
    out: list[Stroke] = []
    for i in range(passes, 0, -1):
        ratio = i / passes
        out.append(
            Stroke(
                color=base.color,
                width=base.width + base.glow * ratio * 1.8,
                alpha=base.alpha * (1.0 - ratio) ** 1.6 * 0.55,
            )
        )
    out.append(Stroke(color=base.color, width=base.width, alpha=base.alpha))
    return out


def scale_points(
    points: Sequence[tuple[float, float]],
    origin: tuple[float, float],
    sx: float,
    sy: float | None = None,
    angle_deg: float = 0.0,
    offset: tuple[float, float] = (0.0, 0.0),
) -> list[tuple[float, float]]:
    """Escala/rota/traslada una nube de puntos alrededor de ``origin``.

    Es la operación que convierte un mismo cuerpo vectorial en las posturas del
    personaje (encoger los ojos al parpadear, aplastar el cuerpo al dormir).
    """
    scale_y = sx if sy is None else sy
    theta = math.radians(angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    out: list[tuple[float, float]] = []
    for px, py in points:
        dx, dy = (px - origin[0]) * sx, (py - origin[1]) * scale_y
        out.append(
            (
                origin[0] + dx * cos_t - dy * sin_t + offset[0],
                origin[1] + dx * sin_t + dy * cos_t + offset[1],
            )
        )
    return out
