"""Adapt the pure notch controller to a guarded, non-intrusive Qt tool window."""

import logging

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCloseEvent, QEnterEvent, QKeyEvent, QMouseEvent, QPaintEvent, QPainter, QScreen
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget

from core.eon_state import STATE_LABELS, state_color
from gui.char_widget import CharWidget
from gui.notch_window import NotchController, NotchGeometryState

logger = logging.getLogger(__name__)
# Initial logical-pixel dimensions, pending Pablo's real-screen visual review.
PANEL_SIZES = {
    NotchGeometryState.PEEK: (220, 5),
    NotchGeometryState.HOVER_PEEK: (220, 27),
    NotchGeometryState.EXPANDED: (220, 90),
}
TICK_INTERVAL_MS = 200


class NotchWindow(QWidget):
    """Render controller state on the primary screen, never loading voice or AI."""

    def __init__(self, controller: NotchController | None = None) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.controller = controller if controller is not None else NotchController()
        self.last_error: str | None = None
        self._screen: QScreen | None = None
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
        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        text_layout.addWidget(self._title)
        text_layout.addWidget(self._status)
        layout = QHBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)
        layout.addWidget(self.character, 0)
        layout.addLayout(text_layout, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        try:
            QApplication.instance().primaryScreenChanged.connect(self._on_primary_screen_changed)
        except Exception as exc:
            self._report_error("No se pudo observar la pantalla principal", exc)
        self.refresh()
        self._timer.start()

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
        try:
            QApplication.instance().primaryScreenChanged.disconnect(self._on_primary_screen_changed)
            if self._screen is not None:
                self._screen.geometryChanged.disconnect(self.refresh)
        except (TypeError, RuntimeError, AttributeError) as exc:
            self._report_error("No se pudieron liberar los observadores de pantalla", exc)
        super().closeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw a rounded state accent and a high-contrast expanded content panel."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(state_color(self.controller.eon_state)))
            bounds = QRectF(self.rect())
            radius = min(16.0, self.height() / 2)
            painter.drawRoundedRect(bounds, radius, radius)
            if self.controller.geometry_state == NotchGeometryState.EXPANDED:
                painter.setBrush(QColor("#10131c"))
                painter.drawRoundedRect(bounds.adjusted(2, 5, -2, -2), 14, 14)
                if self.hasFocus():
                    painter.setPen(QColor("#ffffff"))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(bounds.adjusted(4, 7, -4, -4), 12, 12)
            elif self.controller.geometry_state == NotchGeometryState.HOVER_PEEK:
                painter.setPen(QColor("#10131c"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"Eon · {STATE_LABELS[self.controller.eon_state]}")
        finally:
            painter.end()
