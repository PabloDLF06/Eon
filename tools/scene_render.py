"""Renderizador de escenas ``gui.scene`` con Pillow.

Existe por dos motivos:

1. ``tools/preview.py`` lo usa para producir PNGs de los estados del notch y del
   personaje sin necesidad de monitor, GPU ni Ollama -- así el diseño visual se
   puede revisar en cualquier máquina (y en CI).
2. Es un "segundo par de ojos" para los tests: si una escena no se puede
   rasterizar, hay una primitiva mal formada.

Consume exactamente las mismas primitivas que ``QPainter``, de modo que la
geometría revisada aquí es la geometría real de la aplicación.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path

import numpy as np
from gui.scene import CLIP_START_Z, Paint, Scene, Shape, Stroke, TextShape, hex_to_rgb
from PIL import Image, ImageDraw, ImageFilter, ImageFont

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def find_font(size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont | None:
    """Devuelve la primera fuente TrueType del sistema que exista, o ``None``."""
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), max(6, int(size)))
            except OSError:  # pragma: no cover - fuente corrupta
                continue
    try:
        return ImageFont.load_default(size=max(6, int(size)))
    except TypeError:  # Pillow < 10 no acepta size=
        return ImageFont.load_default()


class SceneRenderer:
    """Rasteriza :class:`gui.scene.Scene` en un buffer RGBA (0-255)."""

    def __init__(self, width: int, height: int, scale: float = 1.0, supersample: int = 2) -> None:
        self.width = int(width)
        self.height = int(height)
        self.scale = float(scale)
        self.ss = max(1, int(supersample))
        self.w = int(round(self.width * self.scale * self.ss))
        self.h = int(round(self.height * self.scale * self.ss))
        self.base = np.zeros((self.h, self.w, 4), dtype=np.float32)
        self._clip_mask: np.ndarray | None = None
        self.fonts: dict[int, object] = {}

    # ------------------------------------------------------------------ API --
    def paint(self, scene: Scene, background: tuple[int, int, int, int] = (10, 10, 14, 255)) -> Image.Image:
        """Pinta la escena sobre ``background`` y devuelve la imagen final.

        Respeta la misma convención de capas que ``gui.qt_paint``: primero el
        aura (``z < CLIP_START_Z``), después el contenido recortado por la
        cápsula. Así el PNG de previsualización es fiel a lo que se ve en pantalla.
        """
        ordered = scene.sorted_shapes()
        for item in ordered:
            if item.z >= CLIP_START_Z:
                continue
            self._draw_item(item)
        content = [item for item in ordered if item.z >= CLIP_START_Z]
        if scene.clip and content:
            self._clip_region = scene.clip
        for item in content:
            self._draw_item(item)
        if scene.clip:
            self._apply_clip(scene.clip)
        out = Image.new("RGBA", (self.w, self.h), background)
        overlay = Image.fromarray(np.clip(self.base, 0, 255).astype("uint8"), "RGBA")
        out.alpha_composite(overlay)
        if self.ss > 1:
            out = out.resize((int(self.width * self.scale), int(self.height * self.scale)), Image.LANCZOS)
        return out

    def _composite(self, layer: np.ndarray) -> None:
        """Mezcla ``layer`` (RGBA float 0-255) con ``over`` de alfa recto.

        Reproducir el mismo algoritmo de composición que ``QPainter`` es lo que
        mantiene fiel la previsualización: sin esto, dibujar encima de una capa
        opaca suma y todo sale blanco quemado.
        """
        sa = np.clip(layer[..., 3:4], 0.0, 255.0) / 255.0
        da = np.clip(self.base[..., 3:4], 0.0, 255.0) / 255.0
        inv = 1.0 - sa
        out_a = sa + da * inv
        out_rgb = np.where(
            out_a > 1e-6,
            (layer[..., :3] * sa + self.base[..., :3] * da * inv) / np.maximum(out_a, 1e-6),
            0.0,
        )
        self.base[..., :3] = out_rgb
        self.base[..., 3:4] = out_a * 255.0

    # ------------------------------------------------------------- internos --
    def _draw_item(self, item) -> None:
        if isinstance(item, TextShape):
            self._draw_text(item)
        else:
            self._draw_shape(item)

    def _sx(self, points: Iterable[tuple[float, float]]) -> list[tuple[int, int]]:
        k = self.scale * self.ss
        return [(int(round(x * k)), int(round(y * k))) for x, y in points]

    def _draw_shape(self, shape: Shape) -> None:
        if not shape.points:
            return
        pts = self._sx(shape.points)
        if len(pts) < 2:
            return
        if shape.fill is not None:
            self._fill_polygon(pts, shape.fill, closed=shape.closed)
        if shape.stroke is not None:
            self._stroke(pts, shape.stroke, closed=shape.closed)

    def _fill_polygon(self, pts: list[tuple[int, int]], paint: Paint, closed: bool = True) -> None:
        if not closed:
            return  # una línea abierta no encierra área que rellenar
        mask = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(mask).polygon(pts, fill=255)
        alpha = np.asarray(mask, dtype=np.float32) / 255.0
        rgb = np.array(hex_to_rgb(paint.color), dtype=np.float32)
        if paint.to_color is not None:
            self._composite(
                self._linear_gradient(pts, rgb, np.array(hex_to_rgb(paint.to_color), np.float32), alpha, paint.alpha)
            )
            return
        if paint.radial:
            self._composite(self._radial(pts, rgb, alpha, paint))
            return
        layer = np.zeros((self.h, self.w, 4), dtype=np.float32)
        layer[..., :3] = rgb
        layer[..., 3] = alpha * paint.alpha * 255.0
        self._composite(layer)

    def _linear_gradient(
        self,
        pts: list[tuple[int, int]],
        top: np.ndarray,
        bottom: np.ndarray,
        alpha: np.ndarray,
        strength: float,
    ) -> np.ndarray:
        ys = np.nonzero(alpha > 0)[0]
        if len(ys) == 0:
            return np.zeros_like(self.base)
        y0, y1 = int(ys.min()), max(int(ys.max()), int(ys.min()) + 1)
        t = np.clip((np.arange(self.h)[:, None] - y0) / max(1, y1 - y0), 0.0, 1.0)
        ramp = top[None, None, :] * (1.0 - t[..., None]) + bottom[None, None, :] * t[..., None]
        color = np.broadcast_to(ramp, (self.h, self.w, 3)).astype(np.float32)
        # leve curva de atenuación: la piel de la referencia clarea arriba
        lum = 1.0 - 0.10 * np.abs(t[..., None] - 0.12)
        layer = np.zeros((self.h, self.w, 4), dtype=np.float32)
        layer[..., :3] = color * lum
        layer[..., 3] = alpha * strength * 255.0
        return layer

    def _radial(self, pts: list[tuple[int, int]], rgb: np.ndarray, alpha: np.ndarray, paint: Paint) -> np.ndarray:
        ys, xs = np.nonzero(alpha > 0)
        if len(ys) == 0:
            return np.zeros_like(self.base)
        k = self.scale * self.ss
        if paint.focal is not None:
            cx, cy = paint.focal[0] * k, paint.focal[1] * k
        else:
            cx, cy = float(xs.mean()), float(ys.mean())
        yy, xx = np.mgrid[0 : self.h, 0 : self.w]
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        span = max(1.0, float(dist[alpha > 0].max()))
        falloff = np.clip(1.0 - dist / span, 0.0, 1.0) ** 1.6
        layer = np.zeros((self.h, self.w, 4), dtype=np.float32)
        layer[..., :3] = rgb
        layer[..., 3] = alpha * falloff * paint.alpha * 255.0
        return layer

    def _stroke(self, pts: list[tuple[int, int]], stroke: Stroke, closed: bool) -> None:
        passes: list[tuple[float, float]] = []
        glow = stroke.glow
        if glow > 0:
            n = stroke.auto_passes()
            for i in range(n, 0, -1):
                ratio = i / n
                passes.append((stroke.width + glow * ratio * 1.8, stroke.alpha * (1.0 - ratio) ** 1.6 * 0.5))
        passes.append((stroke.width, stroke.alpha))
        rgb = np.array(hex_to_rgb(stroke.color), dtype=np.float32)
        for width, alpha in passes:
            layer = self._polyline_layer(pts, rgb, max(1.0, width * self.scale * self.ss), alpha, closed)
            if glow > 0 and width > stroke.width * 1.05:
                layer = np.asarray(
                    Image.fromarray(np.clip(layer, 0, 255).astype("uint8"), "RGBA").filter(
                        ImageFilter.GaussianBlur(radius=max(1.0, glow * self.scale * self.ss * 0.6))
                    ),
                    dtype=np.float32,
                )
            self._composite(layer)

    def _polyline_layer(
        self,
        pts: list[tuple[int, int]],
        rgb: np.ndarray,
        width: float,
        alpha: float,
        closed: bool,
    ) -> np.ndarray:
        mask = Image.new("L", (self.w, self.h), 0)
        draw = ImageDraw.Draw(mask)
        sequence = list(pts) + ([pts[0]] if closed and len(pts) > 2 else [])
        if len(sequence) >= 2:
            draw.line(sequence, fill=255, width=int(max(1, round(width))), joint="curve")
            r = max(1, round(width / 2.0))
            for px, py in (sequence[0], sequence[-1]):
                draw.ellipse([px - r, py - r, px + r, py + r], fill=255)
        arr = np.asarray(mask, dtype=np.float32) / 255.0
        layer = np.zeros((self.h, self.w, 4), dtype=np.float32)
        layer[..., :3] = rgb
        layer[..., 3] = arr * alpha * 255.0
        return layer

    def _draw_text(self, item: TextShape) -> None:
        if not item.text:
            return
        font = self.fonts.get(int(item.size))
        if font is None:
            font = find_font(item.size * self.scale * self.ss)
            self.fonts[int(item.size)] = font
        draw = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        try:
            left, top, right, bottom = draw.textbbox((0, 0), item.text, font=font)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - fuentes raras
            left = top = 0
            right, bottom = len(item.text) * 6, 10
        tw, th = right - left, bottom - top
        x = item.x * self.scale * self.ss
        if item.anchor == "center":
            x -= tw / 2.0
        elif item.anchor == "right":
            x -= tw
        y = item.y * self.scale * self.ss
        if item.baseline == "middle":
            y -= th / 2.0 + top
        elif item.baseline == "bottom":
            y -= th + top
        else:
            y -= top
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((int(x), int(y)), item.text, font=font, fill=(*hex_to_rgb(item.color), int(255 * item.alpha)))
        self._composite(np.asarray(layer, dtype=np.float32))

    def _apply_clip(self, clip_points: tuple[tuple[float, float], ...]) -> None:
        mask = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(mask).polygon(self._sx(clip_points), fill=255)
        factor = np.asarray(mask, dtype=np.float32)[..., None] / 255.0
        self.base *= factor


def render_scene_to_image(
    scene: Scene,
    scale: float = 1.0,
    supersample: int = 2,
    background: tuple[int, int, int, int] | str = "#0a0a0e",
) -> Image.Image:
    """Atajo: rasteriza ``scene`` con fondo opcional (hex o RGBA)."""
    if isinstance(background, str):
        bg = (*hex_to_rgb(background), 255)
    else:
        bg = background
    renderer = SceneRenderer(int(scene.width), int(scene.height), scale=scale, supersample=supersample)
    return renderer.paint(scene, background=bg)  # type: ignore[arg-type]


def checkerboard(size: tuple[int, int]) -> Image.Image:
    """Fondo de tablero para inspeccionar transparencias en las capturas."""
    img = Image.new("RGBA", size, (26, 26, 34, 255))
    draw = ImageDraw.Draw(img)
    step = 16
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            if (x // step + y // step) % 2 == 0:
                draw.rectangle([x, y, x + step, y + step], fill=(34, 34, 44, 255))
    return img


def grid_overlay(img: Image.Image, step: int = 32) -> Image.Image:
    """Rejilla de referencia para comparar proporciones con la captura original."""
    out = img.convert("RGBA").copy()
    draw = ImageDraw.Draw(out)
    tint = (255, 255, 255, 26)
    for x in range(0, out.width, step):
        draw.line([(x, 0), (x, out.height)], fill=tint)
    for y in range(0, out.height, step):
        draw.line([(0, y), (out.width, y)], fill=tint)
    return out


def sample_gradient(colors: list[str], steps: int = 32) -> list[tuple[int, int, int]]:
    """Gradiente muestreado; útil para depurar la paleta de la piel."""
    from gui.scene import mix

    if len(colors) == 1:
        return [hex_to_rgb(colors[0])] * steps
    out: list[tuple[int, int, int]] = []
    seg = (len(colors) - 1) / max(1, steps - 1)
    for i in range(steps):
        idx = min(int(i * seg), len(colors) - 2)
        local = (i * seg) - idx
        out.append(hex_to_rgb(mix(colors[idx], colors[idx + 1], local)))
    return out


def radial_mask(size: int, power: float = 1.6) -> np.ndarray:
    """Máscara radial normalizada usada por efectos de glow en pruebas."""
    yy, xx = np.mgrid[0:size, 0:size]
    c = (size - 1) / 2.0
    dist = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / max(1.0, c)
    return np.clip(1.0 - dist, 0.0, 1.0) ** max(0.2, power)


def circle_points(cx: float, cy: float, radius: float, samples: int = 48) -> list[tuple[float, float]]:
    """Circunferencia muestreada (misma convención que el resto de la escena)."""
    return [
        (cx + radius * math.cos(2.0 * math.pi * i / samples), cy + radius * math.sin(2.0 * math.pi * i / samples))
        for i in range(samples)
    ]
