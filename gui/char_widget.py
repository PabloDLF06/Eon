"""Draw EON's original warm squircle, minimal face, glow and vector state badges.

Artwork is built from original textual requirements, without external images,
icons or sprites. This is complete vector drawing and animated state logic.
"""

import math
import logging
import random
from types import MappingProxyType

from PyQt6.QtCore import QEasingCurve, QPauseAnimation, QPropertyAnimation, QRectF, QSequentialAnimationGroup, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QFocusEvent, QHideEvent, QKeyEvent, QLinearGradient, QMouseEvent, QPaintEvent, QPainter, QPainterPath, QPen, QRadialGradient, QShowEvent
from PyQt6.QtWidgets import QWidget

from core.eon_state import EonState, STATE_LABELS, state_color

COLOR_TRANSITION_MS = 220
ART_PADDING = 6
logger = logging.getLogger(__name__)
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

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = EonState.IDLE
        self._color = QColor(state_color(self._state))
        self._detail_progress = 1.0
        self._weights = {self._state: 1.0}
        self._source_weights = dict(self._weights)
        self._breath_phase = 0.0
        self._blink = 0.0
        self._glance = 0.0
        self._squash = 0.0
        self._recoil = 0.0
        self._keyboard_focus = False
        self.setMinimumSize(48, 48)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Saludar a Eon")
        try:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception:
            logger.exception("No se pudo configurar el cursor del personaje.")
        self.setAccessibleName("Personaje de Eon")
        self.setAccessibleDescription(STATE_LABELS[self._state])
        self._animation = QPropertyAnimation(self, b"display_color", self)
        self._animation.setDuration(COLOR_TRANSITION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._detail_animation = QPropertyAnimation(self, b"detail_progress", self)
        self._detail_animation.setDuration(COLOR_TRANSITION_MS)
        self._detail_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._breath_animation = QPropertyAnimation(self, b"breath_phase", self)
        self._breath_animation.setDuration(3200)
        self._breath_animation.setStartValue(0.0)
        self._breath_animation.setEndValue(math.tau)
        self._breath_animation.setLoopCount(-1)
        self._blink_animation = QSequentialAnimationGroup(self)
        for start, end, duration in ((0.0, 1.0, 85), (1.0, 0.0, 110)):
            animation = QPropertyAnimation(self, b"blink_progress")
            animation.setStartValue(start)
            animation.setEndValue(end)
            animation.setDuration(duration)
            animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
            self._blink_animation.addAnimation(animation)
            if end == 1.0:
                self._blink_animation.addAnimation(QPauseAnimation(40))
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._start_blink)
        self._blink_animation.finished.connect(self._schedule_blink)
        self._glance_animation = QSequentialAnimationGroup(self)
        self._glance_out = QPropertyAnimation(self, b"glance_offset")
        self._glance_out.setDuration(260)
        self._glance_out.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._glance_animation.addAnimation(self._glance_out)
        self._glance_animation.addAnimation(QPauseAnimation(340))
        self._glance_back = QPropertyAnimation(self, b"glance_offset")
        self._glance_back.setDuration(340)
        self._glance_back.setEndValue(0.0)
        self._glance_back.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._glance_animation.addAnimation(self._glance_back)
        self._glance_timer = QTimer(self)
        self._glance_timer.setSingleShot(True)
        self._glance_timer.timeout.connect(self._start_glance)
        self._glance_animation.finished.connect(self._schedule_glance)
        self._squash_animation = self._make_motion(b"squash_amount", 80, 190)
        self._recoil_animation = self._make_motion(b"recoil_amount", 90, 240)

    def _make_motion(self, property_name: bytes, outward_ms: int, return_ms: int) -> QSequentialAnimationGroup:
        group = QSequentialAnimationGroup(self)
        for start, end, duration in ((0.0, 1.0, outward_ms), (1.0, 0.0, return_ms)):
            animation = QPropertyAnimation(self, property_name)
            animation.setStartValue(start)
            animation.setEndValue(end)
            animation.setDuration(duration)
            animation.setEasingCurve(QEasingCurve.Type.InOutSine)
            group.addAnimation(animation)
        return group

    def _get_glance(self) -> float:
        return self._glance

    def _set_glance(self, value: float) -> None:
        self._glance = value
        self.update()

    glance_offset = pyqtProperty(float, fget=_get_glance, fset=_set_glance)

    def _get_squash(self) -> float:
        return self._squash

    def _set_squash(self, value: float) -> None:
        self._squash = value
        self.update()

    squash_amount = pyqtProperty(float, fget=_get_squash, fset=_set_squash)

    def _get_recoil(self) -> float:
        return self._recoil

    def _set_recoil(self, value: float) -> None:
        self._recoil = value
        self.update()

    recoil_amount = pyqtProperty(float, fget=_get_recoil, fset=_set_recoil)

    def _schedule_glance(self) -> None:
        if self.isVisible() and self._state == EonState.IDLE:
            self._glance_timer.start(random.randint(4000, 9000))

    def _start_glance(self) -> None:
        if self.isVisible() and self._state == EonState.IDLE:
            target = random.choice((-2.0, 2.0))
            self._glance_out.setStartValue(self._glance)
            self._glance_out.setEndValue(target)
            self._glance_back.setStartValue(target)
            self._glance_animation.start()

    def _activate(self) -> None:
        self._recoil_animation.stop()
        self._recoil_animation.start()
        self.clicked.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self._activate()
                event.accept()
            else:
                super().mousePressEvent(event)
        except Exception:
            logger.exception("No se pudo procesar el clic del personaje.")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        try:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Space):
                self._activate()
                event.accept()
            else:
                super().keyPressEvent(event)
        except Exception:
            logger.exception("No se pudo procesar la tecla del personaje.")

    def _get_breath_phase(self) -> float:
        return self._breath_phase

    def focusInEvent(self, event: QFocusEvent) -> None:
        self._keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)

    def _set_breath_phase(self, phase: float) -> None:
        self._breath_phase = phase
        self._repaint_artwork()

    breath_phase = pyqtProperty(float, fget=_get_breath_phase, fset=_set_breath_phase)

    def _get_blink_progress(self) -> float:
        return self._blink

    def _set_blink_progress(self, progress: float) -> None:
        self._blink = progress
        self._repaint_artwork()

    blink_progress = pyqtProperty(float, fget=_get_blink_progress, fset=_set_blink_progress)

    def _schedule_blink(self) -> None:
        if self.isVisible():
            self._blink_timer.start(random.randint(3000, 7000))

    def _start_blink(self) -> None:
        if self.isVisible():
            self._blink_animation.start()

    def showEvent(self, event: QShowEvent) -> None:
        if self._state == EonState.IDLE:
            self._breath_animation.start()
        self._schedule_blink()
        self._schedule_glance()
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self._breath_animation.stop()
        self._blink_timer.stop()
        self._blink_animation.stop()
        self._glance_timer.stop()
        self._glance_animation.stop()
        self._squash_animation.stop()
        self._recoil_animation.stop()
        self._set_glance(0.0)
        self._set_squash(0.0)
        self._set_recoil(0.0)
        self._set_breath_phase(0.0)
        self._set_blink_progress(0.0)
        super().hideEvent(event)

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
        self._glance_timer.stop()
        self._glance_animation.stop()
        self._set_glance(0.0)
        self._schedule_glance()
        if self.isVisible():
            self._squash_animation.stop()
            self._squash_animation.start()
        self._breath_animation.stop()
        self._set_breath_phase(0.0)
        if state == EonState.IDLE and self.isVisible():
            self._breath_animation.start()
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
            x += self._glance if state == EonState.IDLE else 0
            if self._blink > 0.85:
                painter.drawLine(round(x - 3), 32, round(x + 3), 32)
            elif state == EonState.IDLE:
                eye = QPainterPath()
                eye.moveTo(x - 3, 31)
                eye.quadTo(x, 29 + 2 * self._blink, x + 3, 31)
                painter.drawPath(eye)
            elif state == EonState.ERROR:
                painter.drawLine(round(x - 3), 29, round(x + 2), 34)
            elif state == EonState.BUILDING:
                painter.drawLine(round(x - 3), 31, round(x + 3), 31)
            else:
                tall = (7 if state in (EonState.LISTENING, EonState.VISION_ACTIVE) else 5) * (1 - self._blink)
                tall = max(0.8, tall)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(x - 2, 30 - tall / 2, 4, tall), 2, 2)
        if state == EonState.SPEAKING:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(46, 39, 4, 3))

    def _paint_artwork(self, painter: QPainter, bounds: QRectF) -> None:
        """Share the same scalable artwork with full widgets and clipped previews."""
        painter.save()
        bounds = bounds.adjusted(ART_PADDING, ART_PADDING, -ART_PADDING, -ART_PADDING)
        scale = min(bounds.width() / 100, bounds.height() / 68)
        painter.translate(bounds.center().x() - 50 * scale, bounds.center().y() - 34 * scale)
        painter.scale(scale, scale)
        breath_scale = 1.0 + 0.01 * (1 - math.cos(self._breath_phase))
        painter.translate(50, 54)
        painter.translate(0, -2 * self._recoil)
        painter.scale(breath_scale * (1 + 0.035 * self._squash),
                      breath_scale * (1 - 0.045 * self._squash - 0.02 * self._recoil))
        painter.translate(-50, -54)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        contact = QRadialGradient(49, 56, 37)
        contact.setColorAt(0, QColor(0, 0, 0, 110))
        contact.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(contact)
        painter.drawEllipse(QRectF(10, 50, 78, 12))
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
        painter.save()
        painter.setClipPath(body)
        shade = QLinearGradient(0, 36, 0, 55)
        shade.setColorAt(0, QColor(0, 0, 0, 0))
        shade.setColorAt(1, QColor(20, 14, 26, 65))
        painter.fillRect(QRectF(11, 36, 76, 19), shade)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 75))
        painter.drawEllipse(QRectF(20, 15, 20, 5))
        painter.restore()
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
            if self.hasFocus() and self._keyboard_focus:
                painter.setPen(QPen(QColor("#f0f2f6"), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        finally:
            painter.end()
