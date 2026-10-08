"""Adapt the pure notch controller to a guarded, non-intrusive Qt tool window."""

import logging
import math

from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRectF, Qt, QTimer, pyqtProperty
from PyQt6.QtGui import QColor, QCloseEvent, QEnterEvent, QKeyEvent, QMouseEvent, QPaintEvent, QPainter, QPainterPath, QRegion, QScreen
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QWidget

from core.eon_state import STATE_LABELS
from gui.char_widget import CharWidget, COLOR_TRANSITION_MS
from gui.notch_window import NotchController, NotchGeometryState

logger = logging.getLogger(__name__)
# Initial logical-pixel dimensions, pending Pablo's real-screen visual review.
PANEL_SIZES = {
    NotchGeometryState.PEEK: (220, 5),
    NotchGeometryState.HOVER_PEEK: (220, 27),
    NotchGeometryState.EXPANDED: (220, 90),
}
TICK_INTERVAL_MS = 200
CAPSULE_COLOR = "#121318"
REVEAL_LEVELS = {
    NotchGeometryState.PEEK: 0.0,
    NotchGeometryState.HOVER_PEEK: 0.38,
    NotchGeometryState.EXPANDED: 1.0,
}


class NotchWindow(QWidget):
    """Render controller state on the primary screen, never loading voice or AI."""

    def __init__(self, controller: NotchController | None = None) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.controller = controller if controller is not None else NotchController()
        self.last_error: str | None = None
        self._screen: QScreen | None = None
        self._visual_geometry = self.controller.geometry_state
        self._reveal = REVEAL_LEVELS[self._visual_geometry]
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Eon — Notch")
        self.setAccessibleName("Panel de estado de Eon")
        try:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception as exc:
            self._report_error("No se pudo configurar el cursor del notch", exc)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.character = CharWidget(self)
        self._title = QLabel("Eon", self)
        self._title.setStyleSheet("color: #f8fafc; font-size: 17px; font-weight: 600;")
        self._status = QLabel(self)
        self._status.setStyleSheet("color: #f8fafc; font-size: 13px;")
        for label in (self._title, self._status):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label_effects = []
        for label in (self._title, self._status):
            effect = QGraphicsOpacityEffect(label)
            label.setGraphicsEffect(effect)
            self._label_effects.append(effect)
        self._reveal_animation = QPropertyAnimation(self, b"reveal_progress", self)
        self._reveal_animation.setDuration(COLOR_TRANSITION_MS)
        self._reveal_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        try:
            QApplication.instance().primaryScreenChanged.connect(self._on_primary_screen_changed)
        except Exception as exc:
            self._report_error("No se pudo observar la pantalla principal", exc)
        self.refresh()
        self._timer.start()

    def _get_reveal_progress(self) -> float:
        """Return interior reveal progress without exposing controller mutations."""
        return self._reveal

    def _set_reveal_progress(self, progress: float) -> None:
        """Apply an animated interior clip/translation frame, not a native resize."""
        self._reveal = progress
        self._apply_reveal_frame()
        self.update()

    reveal_progress = pyqtProperty(float, fget=_get_reveal_progress, fset=_set_reveal_progress)

    def _character_bounds(self) -> QRectF:
        """Keep artwork wide and centered, with only its crest visible at rest."""
        return QRectF((self.width() - 100) / 2, -8 + 8 * self._reveal, 100, 68)

    def _clip_height(self) -> float:
        """Grow the drawing clip inside the state-sized native viewport."""
        return min(float(self.height()), 5 + 85 * self._reveal)

    def _apply_reveal_frame(self) -> None:
        """Clip the full child and fade labels using the same progress as previews."""
        bounds = self._character_bounds()
        self.character.setGeometry(round(bounds.x()), round(bounds.y()), 100, 68)
        visible_height = max(0, min(68, math.ceil(self._clip_height() - bounds.y())))
        self.character.setMask(QRegion(0, 0, 100, visible_height))
        self._title.setGeometry(max(0, self.width() // 2 - 65), 67, 40, 22)
        self._status.setGeometry(max(0, self.width() // 2 - 20), 69, max(1, self.width() // 2 + 15), 20)
        opacity = max(0.0, min(1.0, (self._reveal - 0.55) / 0.45))
        for effect in self._label_effects:
            effect.setOpacity(opacity)

    def _report_error(self, operation: str, exc: Exception) -> None:
        """Expose and log peripheral errors without crashing the event loop."""
        self.last_error = f"{operation}: {exc}"
        logger.exception("%s.", operation)

    def _on_primary_screen_changed(self, screen: QScreen | None) -> None:
        """Re-evaluate placement after primary-screen selection changes."""
        self.refresh()

    def _position_on_primary_screen(self) -> None:
        """Read current QScreen geometry in logical pixels and handle hot-plug errors."""
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                raise RuntimeError("No hay pantalla principal disponible.")
            if screen is not self._screen:
                if self._screen is not None:
                    try:
                        self._screen.geometryChanged.disconnect(self.refresh)
                    except (TypeError, RuntimeError):
                        logger.warning("No se pudo desconectar una pantalla retirada.", exc_info=True)
                self._screen = screen
                screen.geometryChanged.connect(self.refresh)
            area = screen.geometry()
            if area.width() <= 0 or area.height() <= 0:
                raise RuntimeError("La pantalla devolvió una geometría vacía.")
            width, height = PANEL_SIZES[self.controller.geometry_state]
            self.setFixedSize(min(width, area.width()), min(height, area.height()))
            self.move(area.x() + (area.width() - self.width()) // 2, area.y())
            self.last_error = None
        except Exception as exc:
            self._report_error("No se pudo posicionar el notch en la pantalla", exc)

    def refresh(self, *args: object) -> None:
        """Advance pure logic then synchronize visibility, focus, color and position."""
        try:
            self.controller.tick()
            expanded = self.controller.geometry_state == NotchGeometryState.EXPANDED
            should_block_focus = not expanded
            if bool(self.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus) != should_block_focus:
                was_visible = self.isVisible()
                self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, should_block_focus)
                self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if expanded else Qt.FocusPolicy.NoFocus)
                if was_visible:
                    self.show()
            self.character.setVisible(expanded)
            self._title.setVisible(expanded)
            self._status.setVisible(expanded)
            self.character.set_eon_state(self.controller.eon_state)
            label = STATE_LABELS[self.controller.eon_state]
            self._status.setText(label)
            self.setAccessibleDescription(label)
            self._position_on_primary_screen()
            if self._visual_geometry != self.controller.geometry_state:
                self._visual_geometry = self.controller.geometry_state
                self._reveal_animation.stop()
                self._reveal_animation.setStartValue(self._reveal)
                self._reveal_animation.setEndValue(REVEAL_LEVELS[self._visual_geometry])
                self._reveal_animation.start()
            self._apply_reveal_frame()
            self.update()
        except Exception as exc:
            self._report_error("No se pudo actualizar el notch", exc)

    def enterEvent(self, event: QEnterEvent) -> None:
        """Translate a mouse entry safely without activating the window."""
        try:
            self.controller.on_mouse_enter()
            self.refresh()
        except Exception as exc:
            self._report_error("No se pudo procesar la entrada del ratón", exc)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Translate mouse departure and restart eligible idle timing."""
        try:
            self.controller.on_mouse_leave()
            self.refresh()
        except Exception as exc:
            self._report_error("No se pudo procesar la salida del ratón", exc)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Use a left click for expansion; only an already-expanded panel takes focus."""
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                was_expanded = self.controller.geometry_state == NotchGeometryState.EXPANDED
                self.controller.on_click()
                self.refresh()
                if was_expanded:
                    self.setFocus(Qt.FocusReason.MouseFocusReason)
                event.accept()
            else:
                event.ignore()
        except Exception as exc:
            self._report_error("No se pudo procesar el clic del ratón", exc)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Provide a local Escape collapse without registering a global hotkey."""
        try:
            if event.key() == Qt.Key.Key_Escape:
                self.controller.collapse()
                self.refresh()
                event.accept()
            else:
                super().keyPressEvent(event)
        except Exception as exc:
            self._report_error("No se pudo procesar la tecla del notch", exc)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop periodic work and release screen observers when the window closes."""
        self._timer.stop()
        self._reveal_animation.stop()
        try:
            QApplication.instance().primaryScreenChanged.disconnect(self._on_primary_screen_changed)
            if self._screen is not None:
                self._screen.geometryChanged.disconnect(self.refresh)
        except (TypeError, RuntimeError, AttributeError) as exc:
            self._report_error("No se pudieron liberar los observadores de pantalla", exc)
        super().closeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint a fixed dark camera-cutout capsule and clipped original artwork."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            capsule_height = min(max(2.0, self.height() - 3.0), 2 + 88 * self._reveal)
            radius = min(16.0, capsule_height / 2)
            right = float(self.width())
            capsule = QPainterPath()
            capsule.moveTo(0, 0)
            capsule.lineTo(right, 0)
            capsule.lineTo(right, capsule_height - radius)
            capsule.quadTo(right, capsule_height, right - radius, capsule_height)
            capsule.lineTo(radius, capsule_height)
            capsule.quadTo(0, capsule_height, 0, capsule_height - radius)
            capsule.closeSubpath()
            painter.setBrush(QColor(CAPSULE_COLOR))
            painter.drawPath(capsule)
            if self.controller.geometry_state != NotchGeometryState.EXPANDED:
                clip = QPainterPath()
                clip.addRect(QRectF(0, 0, self.width(), self._clip_height()))
                painter.setClipPath(clip)
                self.character._paint_artwork(painter, self._character_bounds())
            if self.hasFocus() and self.controller.geometry_state == NotchGeometryState.EXPANDED:
                painter.setPen(QColor("#f8fafc"))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(capsule)
        finally:
            painter.end()
