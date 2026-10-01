"""Pintor de escenas ``gui.scene`` sobre ``QPainter``.

Es la otra mitad del contrato: ``gui/char_kinematics.py`` y
``gui/notch_layout.py`` producen primitivas, aquí se convierten a trazados de Qt.
Mantener esta traducción en un único módulo evita que el notch, el personaje y
el glow de pantalla reinventen cada uno cómo se pinta un degradado o un halo de
neón (y que diverjan).

Todo lo público es seguro de llamar dentro de un ``paintEvent``.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from gui.scene import CLIP_START_Z, Paint, Scene, Shape, Stroke, TextShape, hex_to_rgb

__all__ = ["paint_scene", "paint_shape", "paint_text", "path_from_points"]


def _color(hex_color: str, alpha: float = 1.0) -> QColor:
    r, g, b = hex_to_rgb(hex_color)
    return QColor(r, g, b, int(max(0.0, min(1.0, alpha)) * 255))


def path_from_points(points: Sequence[tuple[float, float]], closed: bool = True) -> QPainterPath:
    """Convierte una polilínea muestreada en un trazado suave de Qt."""
    path = QPainterPath()
    if not points:
        return path
    first = QPointF(float(points[0][0]), float(points[0][1]))
    path.moveTo(first)
    for x, y in points[1:]:
        path.lineTo(QPointF(float(x), float(y)))
    if closed:
        path.closeSubpath()
    return path


def _brush_for(paint: Paint, bounds: tuple[float, float, float, float]) -> QBrush:
    x, y, w, h = bounds
    if paint.to_color is not None:
        gradient = QLinearGradient(x, y, x, y + h)
        gradient.setColorAt(0.0, _color(paint.color, 1.0))
        # punto medio ligeramente más claro: da volumen sin necesidad de textura
        gradient.setColorAt(0.42, _color(paint.color, 1.0).lighter(103))
        gradient.setColorAt(1.0, _color(paint.to_color, 1.0))
        brush = QBrush(gradient)
        brush.setColor(_color(paint.color, paint.alpha))
        return brush
    if paint.radial:
        cx, cy = (x + w / 2.0, y + h / 2.0) if paint.focal is None else paint.focal
        radius = max(w, h) * 0.85
        gradient = QRadialGradient(QPointF(cx, cy), radius, QPointF(cx, cy))
        gradient.setColorAt(0.0, _color(paint.color, paint.alpha))
        gradient.setColorAt(0.62, _color(paint.color, paint.alpha * 0.42))
        gradient.setColorAt(1.0, _color(paint.color, 0.0))
        return QBrush(gradient)
    return QBrush(_color(paint.color, paint.alpha))


def _pen_for(stroke: Stroke, glow_scale: float = 1.0) -> QPen:
    pen = QPen(_color(stroke.color, stroke.alpha * glow_scale))
    pen.setWidthF(max(0.4, stroke.width))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCosmetic(False)
    return pen


def paint_shape(painter: QPainter, shape: Shape) -> None:
    """Pinta una primitiva rellena y/o trazada, con su halo de neón si procede."""
    if not shape.points:
        return
    path = path_from_points(shape.points, shape.closed)
    xs = [p[0] for p in shape.points]
    ys = [p[1] for p in shape.points]
    bounds = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    if shape.fill is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_brush_for(shape.fill, bounds))
        painter.drawPath(path)

    if shape.stroke is not None:
        stroke = shape.stroke
        passes = stroke.auto_passes()
        if passes:
            # halo: N trazos cada vez más gruesos y más transparentes. Más
            # barato que un QGraphicsBlurEffect y no fuerza un backing-store.
            for i in range(passes, 0, -1):
                ratio = i / passes
                halo = Stroke(
                    color=stroke.color,
                    width=stroke.width + stroke.glow * ratio * 1.8,
                    alpha=stroke.alpha * (1.0 - ratio) ** 1.6 * 0.55,
                )
                painter.setPen(_pen_for(halo, 1.0))
                painter.drawPath(path)
        painter.setPen(_pen_for(stroke))
        painter.drawPath(path)


def paint_text(painter: QPainter, item: TextShape) -> None:
    """Texto con anclaje propio (no depende del estado del pintor)."""
    if not item.text:
        return
    font = QFont()
    if item.family:
        font.setFamily(item.family)
    else:
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans", "Arial"])
    font.setPixelSize(max(7, int(round(item.size))))
    font.setBold(item.bold)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    painter.setFont(font)
    painter.setPen(_color(item.color, item.alpha))

    metrics = painter.fontMetrics()
    width = metrics.horizontalAdvance(item.text)
    ascent, descent = metrics.ascent(), metrics.descent()
    x = item.x
    if item.anchor == "center":
        x -= width / 2.0
    elif item.anchor == "right":
        x -= width
    # ``drawText`` coloca el origen en la línea base; estas tres fórmulas
    # convierten el anclaje semántico de la escena en esa coordenada.
    if item.baseline == "middle":
        y = item.y + (ascent - descent) / 2.0
    elif item.baseline == "bottom":
        y = item.y
    else:  # "top"
        y = item.y + ascent
    painter.drawText(QPointF(x, y), item.text)


def paint_scene(painter: QPainter, scene: Scene, offset: tuple[float, float] = (0.0, 0.0)) -> None:
    """Pinta la escena completa aplicando su recorte (la cápsula).

    ``offset`` permite reutilizar la misma escena dentro de una ventana más
    pequeña: el notch pinta la franja completa de la pantalla, pero la ventana
    sólo cubre lo que ocupa la cápsula, así que restamos el origen de la banda.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    if offset != (0.0, 0.0):
        painter.translate(offset[0], offset[1])
    painter.setCompositionMode(QPainter.CompositionMode.SourceOver)
    ordered = scene.sorted_shapes()
    aura = [item for item in ordered if item.z < CLIP_START_Z]
    content = [item for item in ordered if item.z >= CLIP_START_Z]
    for item in aura:  # el aura puede desbordar la cápsula
        if isinstance(item, TextShape):
            paint_text(painter, item)
        else:
            paint_shape(painter, item)
    if scene.clip:
        clip = path_from_points(scene.clip, closed=True)
        if not clip.isEmpty():
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    for item in content:
        if isinstance(item, TextShape):
            paint_text(painter, item)
        else:
            paint_shape(painter, item)
    painter.restore()


def fill_background(painter: QPainter, color: str = "#000000", alpha: float = 1.0) -> None:
    """Fondo opaco para la cápsula (el negro del notch no debe translucir)."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_color(color, alpha))
    painter.drawRect(painter.viewport())
    painter.restore()
