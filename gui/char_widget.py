"""Draw EON's original warm squircle, minimal face, glow and vector state badges.

Artwork is built from original textual requirements, without external images,
icons or sprites. This is complete vector drawing and animated state logic.
"""

import math
from types import MappingProxyType

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QLinearGradient, QPaintEvent, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

from core.eon_state import EonState, STATE_LABELS, state_color

COLOR_TRANSITION_MS = 220
WARM_TONE = "#e9d3b8"
BADGE_DESIGNS = MappingProxyType({
    EonState.IDLE: "Círculo con guion corto",
    EonState.LISTENING: "Disco con tres barras de distinta altura",
    EonState.THINKING: "Rombo con tres puntos",
    EonState.SPEAKING: "Círculo con dos ondas curvas",
    EonState.BUILDING: "Cuadrado redondeado con bloques escalonados",
    EonState.ERROR: "Hexágono con dos trazos cruzados",
    EonState.VISION_ACTIVE: "Cápsula con cuatro marcas de encuadre",
})


def _squircle_path(bounds: QRectF, exponent: float = 4.5) -> QPainterPath:
    """Build an original wide superellipse outline from normalized coordinates."""
    path = QPainterPath()
    for index in range(97):
        angle = index * math.tau / 96
        cosine, sine = math.cos(angle), math.sin(angle)
        x = bounds.center().x() + bounds.width() / 2 * math.copysign(abs(cosine) ** (2 / exponent), cosine)
        y = bounds.center().y() + bounds.height() / 2 * math.copysign(abs(sine) ** (2 / exponent), sine)
        if index == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _blend(first: QColor, second: QColor, weight: float) -> QColor:
    """Blend actual color channels without substituting a state palette."""
    return QColor.fromRgbF(*(a + (b - a) * weight for a, b in zip(first.getRgbF(), second.getRgbF())))


class CharWidget(QWidget):
    """Animate original vector artwork while keeping the public state API intact."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = EonState.IDLE
        self._color = QColor(state_color(self._state))
        self._detail_progress = 1.0
        self._weights = {self._state: 1.0}
        self._source_weights = dict(self._weights)
        self.setMinimumSize(48, 48)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Personaje de Eon")
        self.setAccessibleDescription(STATE_LABELS[self._state])
        self._animation = QPropertyAnimation(self, b"display_color", self)
        self._animation.setDuration(COLOR_TRANSITION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._detail_animation = QPropertyAnimation(self, b"detail_progress", self)
        self._detail_animation.setDuration(COLOR_TRANSITION_MS)
        self._detail_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _repaint_artwork(self) -> None:
        """Update this widget and any parent-painted partial preview together."""
        self.update()
        if self.parentWidget() is not None:
            self.parentWidget().update()

    def _get_display_color(self) -> QColor:
        """Return a copy so animation cannot mutate the stored color externally."""
        return QColor(self._color)

    def _set_display_color(self, color: QColor) -> None:
        """Apply an interpolated animation frame and request painting."""
        self._color = QColor(color)
        self._repaint_artwork()

    display_color = pyqtProperty(QColor, fget=_get_display_color, fset=_set_display_color)

    def _get_detail_progress(self) -> float:
        """Expose the current crossfade frame to Qt's animation system."""
        return self._detail_progress

    def _set_detail_progress(self, progress: float) -> None:
        """Crossfade face and badge layers from their current interrupted mixture."""
        self._detail_progress = progress
        self._weights = {state: weight * (1 - progress) for state, weight in self._source_weights.items()}
        self._weights[self._state] = self._weights.get(self._state, 0.0) + progress
        self._repaint_artwork()

    detail_progress = pyqtProperty(float, fget=_get_detail_progress, fset=_set_detail_progress)

    def set_eon_state(self, state: EonState) -> None:
        """Transition to the selected state's color from the current visible frame."""
        target = QColor(state_color(state))
        if state == self._state:
            return
        self._detail_animation.stop()
        self._source_weights = dict(self._weights)
        self._state = state
        self.setAccessibleDescription(STATE_LABELS[state])
        self._animation.stop()
        self._animation.setStartValue(QColor(self._color))
        self._animation.setEndValue(target)
        self._animation.start()
        self._detail_animation.setStartValue(0.0)
        self._detail_animation.setEndValue(1.0)
        self._detail_animation.start()

    def _draw_badge(self, painter: QPainter, state: EonState) -> None:
        """Draw one badge from geometric primitives, with no icon font or asset."""
        painter.save()
        painter.translate(83, 49)
        tint = QColor(state_color(state))
        shape = QPainterPath()
        if state == EonState.THINKING:
            shape.moveTo(0, -10)
            for x, y in ((10, 0), (0, 10), (-10, 0)):
                shape.lineTo(x, y)
            shape.closeSubpath()
        elif state == EonState.ERROR:
            for index in range(6):
                angle = index * math.tau / 6
                point = (10 * math.cos(angle), 10 * math.sin(angle))
                if index == 0:
                    shape.moveTo(*point)
                else:
                    shape.lineTo(*point)
            shape.closeSubpath()
        elif state in (EonState.BUILDING, EonState.VISION_ACTIVE):
            shape.addRoundedRect(QRectF(-10, -9, 20, 18), 4 if state == EonState.BUILDING else 9, 4 if state == EonState.BUILDING else 9)
        else:
            shape.addEllipse(QRectF(-10, -10, 20, 20))
        painter.setPen(QPen(tint.lighter(135), 1.0))
        painter.setBrush(_blend(QColor("#121318"), tint, 0.82))
        painter.drawPath(shape)
        pen = QPen(QColor("#121318"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor("#121318"))
        if state == EonState.IDLE:
            painter.drawLine(-3, 0, 3, 0)
        elif state == EonState.LISTENING:
            for x, height in ((-4, 3), (0, 6), (4, 4)):
                painter.drawLine(x, -height, x, height)
        elif state == EonState.THINKING:
            painter.setPen(Qt.PenStyle.NoPen)
            for x in (-4, 0, 4):
                painter.drawEllipse(QRectF(x - 1.1, -1.1, 2.2, 2.2))
        elif state == EonState.SPEAKING:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for x in (-3, 2):
                wave = QPainterPath()
                wave.moveTo(x, -5)
                wave.cubicTo(x + 5, -3, x + 5, 3, x, 5)
                painter.drawPath(wave)
        elif state == EonState.BUILDING:
            painter.setPen(Qt.PenStyle.NoPen)
            for x, y in ((-5, 2), (-1, -1), (3, -4)):
                painter.drawRoundedRect(QRectF(x, y, 3, 3), 0.7, 0.7)
        elif state == EonState.ERROR:
            painter.drawLine(-4, -4, 4, 4)
            painter.drawLine(4, -4, -4, 4)
        else:
            for x, y, dx, dy in ((-5, -4, 1, 1), (5, -4, -1, 1), (-5, 4, 1, -1), (5, 4, -1, -1)):
                painter.drawLine(x, y, x + dx * 3, y)
                painter.drawLine(x, y, x, y + dy * 3)
        painter.restore()

    def _draw_face(self, painter: QPainter, state: EonState) -> None:
        """Use two small eyes with restrained state-specific expressions."""
        painter.setPen(QPen(QColor("#27232a"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QColor("#27232a"))
        for x in (37, 59):
            if state == EonState.IDLE:
                eye = QPainterPath()
                eye.moveTo(x - 3, 32)
                eye.quadTo(x, 35, x + 3, 32)
                painter.drawPath(eye)
            elif state == EonState.ERROR:
                painter.drawLine(x - 3, 29, x + 2, 34)
            elif state == EonState.BUILDING:
                painter.drawLine(x - 3, 31, x + 3, 31)
            else:
                tall = 7 if state in (EonState.LISTENING, EonState.VISION_ACTIVE) else 5
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(x - 2, 30 - tall / 2, 4, tall), 2, 2)
        if state == EonState.SPEAKING:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(46, 39, 4, 3))

    def _paint_artwork(self, painter: QPainter, bounds: QRectF) -> None:
        """Share the same scalable artwork with full widgets and clipped previews."""
        painter.save()
        scale = min(bounds.width() / 100, bounds.height() / 68)
        painter.translate(bounds.center().x() - 50 * scale, bounds.center().y() - 34 * scale)
        painter.scale(scale, scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QRadialGradient(50, 33, 51)
        glow_color = QColor(self._color)
        glow_color.setAlpha(100)
        glow.setColorAt(0.2, glow_color)
        glow_color.setAlpha(0)
        glow.setColorAt(1.0, glow_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(0, -1, 100, 68))
        body = _squircle_path(QRectF(11, 10, 76, 44))
        gradient = QLinearGradient(22, 10, 75, 55)
        gradient.setColorAt(0, QColor(WARM_TONE))
        gradient.setColorAt(0.52, _blend(QColor(WARM_TONE), self._color, 0.46))
        gradient.setColorAt(1, _blend(QColor(WARM_TONE), self._color, 0.82))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(255, 246, 228, 150), 1.0))
        painter.drawPath(body)
        for state, weight in self._weights.items():
            if weight <= 0:
                continue
            painter.save()
            painter.setOpacity(painter.opacity() * weight)
            self._draw_face(painter, state)
            self._draw_badge(painter, state)
            painter.restore()
        painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint scalable vector artwork without reading hardware or external assets."""
        painter = QPainter(self)
        try:
            self._paint_artwork(painter, QRectF(self.rect()))
        finally:
            painter.end()
