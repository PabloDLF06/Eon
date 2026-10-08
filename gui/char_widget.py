"""Draw a working animated eye: artistic-content placeholder, not incomplete logic.

There are no character sprites in assets/char/ yet. This vector artwork is the
explicitly permitted temporary artistic content; state and animation are real.
"""

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QPaintEvent, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from core.eon_state import EonState, STATE_LABELS, state_color

COLOR_TRANSITION_MS = 220


class CharWidget(QWidget):
    """Animate a stylized eye's color, keeping Spanish accessible state labels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = EonState.IDLE
        self._color = QColor(state_color(self._state))
        self.setMinimumSize(48, 48)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Personaje de Eon")
        self.setAccessibleDescription(STATE_LABELS[self._state])
        self._animation = QPropertyAnimation(self, b"display_color", self)
        self._animation.setDuration(COLOR_TRANSITION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _get_display_color(self) -> QColor:
        """Return a copy so animation cannot mutate the stored color externally."""
        return QColor(self._color)

    def _set_display_color(self, color: QColor) -> None:
        """Apply an interpolated animation frame and request painting."""
        self._color = QColor(color)
        self.update()

    display_color = pyqtProperty(QColor, fget=_get_display_color, fset=_set_display_color)

    def set_eon_state(self, state: EonState) -> None:
        """Transition to the selected state's color from the current visible frame."""
        target = QColor(state_color(state))
        if state == self._state:
            return
        self._state = state
        self.setAccessibleDescription(STATE_LABELS[state])
        self._animation.stop()
        self._animation.setStartValue(QColor(self._color))
        self._animation.setEndValue(target)
        self._animation.start()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint scalable vector artwork without reading hardware or external assets."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            diameter = min(self.width(), self.height()) - 8.0
            eye = QRectF((self.width() - diameter) / 2, (self.height() - diameter) / 2, diameter, diameter)
            painter.setPen(QPen(self._color, 2.0))
            painter.setBrush(QColor("#18202f"))
            painter.drawEllipse(eye)
            iris = eye.adjusted(diameter * 0.23, diameter * 0.23, -diameter * 0.23, -diameter * 0.23)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._color)
            painter.drawEllipse(iris)
            painter.setBrush(QColor("#10131c"))
            painter.drawEllipse(iris.adjusted(iris.width() * 0.27, iris.height() * 0.27, -iris.width() * 0.27, -iris.height() * 0.27))
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(QRectF(iris.x() + 3, iris.y() + 3, 5, 5))
        finally:
            painter.end()
