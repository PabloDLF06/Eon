"""Provide original painted controls, card settings and cancellable audio metering."""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPaintEvent, QPainter, QPainterPath, QPen, QResizeEvent
from PyQt6.QtWidgets import (QAbstractSpinBox, QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
                            QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
                            QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
                            QWidget)

import config

if TYPE_CHECKING:
    from core.acoustic_detector import AcousticDetector

logger = logging.getLogger(__name__)

EON_STYLE = """
QDialog {background: #121318; color: #f0f2f6;}
QWidget {font-size: 13px;}
QLabel {color: #f0f2f6; background: transparent;}
QFrame#card {background: #1c1f28; border: 1px solid #424654; border-radius: 12px;}
QPushButton, QComboBox, QSpinBox, QLineEdit {
    color: #f0f2f6; background: #171a22; border: 1px solid #424654;
    border-radius: 8px; padding: 7px 10px; min-height: 20px;
}
QPushButton:hover, QComboBox:hover {background: #292d37; border-color: #8ea9c7;}
QPushButton:pressed, QPushButton:checked {background: #313745;}
QPushButton:focus, QComboBox:focus, QSpinBox:focus, QLineEdit:focus {border-color: #f0f2f6;}
QPushButton:disabled {color: #a5aaba;}
QComboBox::drop-down {border: 0; width: 24px;}
QComboBox QAbstractItemView {background: #1c1f28; color: #f0f2f6; selection-background-color: #424654;}
QScrollArea, QScrollArea > QWidget > QWidget {background: transparent; border: 0;}
QScrollBar:vertical {background: #171a22; width: 8px; margin: 0;}
QScrollBar:horizontal {background: #171a22; height: 6px; margin: 0;}
QScrollBar::handle {background: #636c7e; border-radius: 3px; min-width: 24px; min-height: 24px;}
QScrollBar::add-line, QScrollBar::sub-line {width: 0; height: 0;}
QScrollBar::add-page, QScrollBar::sub-page {background: transparent;}
QToolTip {background: #1c1f28; color: #f0f2f6; border: 1px solid #636c7e; padding: 6px;}
"""


class ToggleSwitch(QCheckBox):
    """Draw an original keyboard-accessible toggle without platform checkbox chrome."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(52, 36)
        self.setAccessibleName("Ocultar automáticamente en reposo")
        self.setToolTip(self.accessibleName())

    def hitButton(self, position: QPoint) -> bool:
        return self.rect().contains(position)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#f0f2f6" if self.hasFocus() else "#636c7e"), 1))
            painter.setBrush(QColor("#385776" if self.isChecked() else "#171a22"))
            painter.drawRoundedRect(QRectF(2, 6, 48, 24), 12, 12)
            painter.setBrush(QColor("#f0f2f6"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(29 if self.isChecked() else 5, 9, 18, 18))
        finally:
            painter.end()


class PeakLevelBar(QProgressBar):
    """Paint a peak marker separately from the instantaneous perceptual level."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.peak = 0.0
        self.setRange(0, 1000)
        self.setTextVisible(False)
        self.setFixedHeight(10)
        self.setAccessibleName("Nivel real de micrófono")
        self.setStyleSheet("QProgressBar {background: #161a23; border: 1px solid #424654; border-radius: 4px;}"
                           "QProgressBar::chunk {background: #00e5ff; border-radius: 3px;}")

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.peak > 0:
            painter = QPainter(self)
            try:
                painter.setPen(QPen(QColor("#f0f2f6"), 2))
                x = 2 + round(self.peak * max(0, self.width() - 5))
                painter.drawLine(x, 2, x, self.height() - 3)
            finally:
                painter.end()


class ShortcutColorButton(QPushButton):
    """Draw a small color dot inside a neutral, keyboard-focusable target."""

    def __init__(self) -> None:
        super().__init__()
        self.color = QColor("#8ea9c7")
        self.setFixedSize(36, 36)
        self.setAccessibleName("Elegir color del acceso")

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#292d37" if self.underMouse() else "#171a22"))
            painter.setPen(QPen(QColor("#f0f2f6" if self.hasFocus() else "#424654"), 1))
            painter.drawRoundedRect(QRectF(1, 1, 34, 34), 8, 8)
            painter.setBrush(self.color)
            painter.setPen(QPen(QColor("#f0f2f6"), 1))
            painter.drawEllipse(QRectF(11, 11, 14, 14))
        finally:
            painter.end()


class SpeechBubble(QLabel):
    """Paint a small original left-pointing speech surface without an image asset."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setStyleSheet("color: #f0f2f6; background: transparent; padding: 6px 12px;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(6, 1, self.width() - 7, self.height() - 2), 9, 9)
            tail = QPainterPath()
            middle = self.height() / 2
            tail.moveTo(1, middle)
            tail.lineTo(8, middle - 4)
            tail.lineTo(8, middle + 4)
            tail.closeSubpath()
            painter.setBrush(QColor("#292d37"))
            painter.setPen(QPen(QColor("#8ea9c7"), 1))
            painter.drawPath(shape.united(tail))
        finally:
            painter.end()
        super().paintEvent(event)


class ShortcutCard(QFrame):
    """Keep literal shortcut data in an editable card, never a command line."""

    def __init__(self, dialog: SettingsDialog, shortcut: dict[str, str]) -> None:
        super().__init__()
        self.setObjectName("card")
        self.dialog = dialog
        self.path = shortcut["path"]
        self.color = shortcut["color"]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        row = QHBoxLayout()
        self.color_button = ShortcutColorButton()
        self.color_button.setToolTip("Elegir color")
        self.label = QLineEdit(shortcut["label"])
        self.label.setMinimumWidth(1)
        self.label.setAccessibleName("Nombre del acceso")
        self.label.setToolTip("Nombre del acceso directo")
        remove = QPushButton("Eliminar")
        remove.setAccessibleName("Eliminar este acceso directo")
        row.addWidget(self.color_button)
        row.addWidget(self.label, 1)
        row.addWidget(remove)
        layout.addLayout(row)
        self.path_label = QLabel()
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setMinimumWidth(1)
        self.path_label.setStyleSheet("color: #b9c1d0;")
        layout.addWidget(self.path_label)
        actions = QHBoxLayout()
        for text, action in (("Elegir archivo", dialog.choose_file), ("Elegir carpeta", dialog.choose_directory)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, callback=action: self._select_and_call(callback))
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.color_button.clicked.connect(lambda: self._select_and_call(dialog.choose_color))
        remove.clicked.connect(lambda: self._select_and_call(dialog.remove_shortcut))
        self._refresh_data()

    def _select_and_call(self, callback: Callable[[], None]) -> None:
        self.dialog._selected_card = self
        callback()

    def _refresh_data(self) -> None:
        self.color_button.color = QColor(self.color)
        self.color_button.setToolTip(f"Elegir color: {self.color}")
        self.color_button.update()
        self.path_label.setToolTip(self.path)
        self.path_label.setAccessibleDescription(self.path)
        self.path_label.setText(self.path_label.fontMetrics().elidedText(
            self.path or "Elige un archivo o carpeta", Qt.TextElideMode.ElideMiddle,
            max(1, self.path_label.width())))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_data()

    def data(self) -> dict[str, str]:
        return {"label": self.label.text(), "path": self.path, "color": self.color}


def launch_shortcut(path: str) -> tuple[bool, str]:
    """Launch a configured absolute path without a shell; never swallow a failure."""
    try:
        target = Path(path)
        if not target.is_absolute() or not target.exists():
            raise FileNotFoundError("La ruta del acceso directo no existe.")
        command = [str(target)] if target.suffix.lower() in (".exe", ".com") else [
            "explorer.exe" if os.name == "nt" else "xdg-open", str(target)]
        subprocess.Popen(command, shell=False)
        logger.info("Acceso directo abierto: %s", target)
        return True, "Acceso directo abierto."
    except Exception:
        logger.exception("No se pudo abrir el acceso directo.")
        return False, "No se pudo abrir el acceso directo. Revisa la ruta y los permisos."


class IconButton(QPushButton):
    """Paint small original gear, microphone and send icons with visible focus."""

    def __init__(self, kind: str, label: str, parent: QWidget | None = None) -> None:
        if kind not in ("gear", "microphone", "send"):
            raise ValueError("Tipo de botón no válido.")
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(44, 44)
        self.setToolTip(label)
        self.setAccessibleName(label)
        try:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception:
            logger.exception("No se pudo configurar el cursor del botón.")

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#313745" if self.isChecked() else
                                    "#292d37" if self.underMouse() else "#1c1f28"))
            painter.setPen(QPen(QColor("#f0f2f6" if self.hasFocus() else "#424654"), 1))
            painter.drawRoundedRect(QRectF(1, 1, 42, 42), 10, 10)
            painter.translate(22, 22)
            painter.setPen(QPen(QColor("#f0f2f6" if self.isEnabled() else "#9094a1"), 1.8,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self.kind == "gear":
                shape = QPainterPath()
                for index in range(48):
                    angle = index * math.tau / 48
                    radius = 9 if index % 6 in (0, 1, 2) else 6.7
                    x, y = radius * math.cos(angle), radius * math.sin(angle)
                    if index == 0:
                        shape.moveTo(x, y)
                    else:
                        shape.lineTo(x, y)
                shape.closeSubpath()
                painter.drawPath(shape)
                painter.drawEllipse(QRectF(-3, -3, 6, 6))
            elif self.kind == "microphone":
                painter.drawRoundedRect(QRectF(-3, -9, 6, 12), 3, 3)
                painter.drawArc(QRectF(-7, -6, 14, 14), 180 * 16, 180 * 16)
                painter.drawLine(0, 8, 0, 11)
                painter.drawLine(-4, 11, 4, 11)
            else:
                shape = QPainterPath()
                shape.moveTo(-8, -8)
                shape.lineTo(10, 0)
                shape.lineTo(-8, 8)
                shape.lineTo(-3, 0)
                shape.closeSubpath()
                painter.drawPath(shape)
                painter.drawLine(-3, 0, 10, 0)
        finally:
            painter.end()


class SettingsDialog(QDialog):
    """Edit actual notch preferences and shortcuts, publishing only validated data."""

    saved = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuración del notch de EON")
        self.setStyleSheet(EON_STYLE)
        self.resize(520, 560)
        try:
            area = self.screen().availableGeometry()
            self.resize(min(520, max(1, area.width() - 24)), min(560, max(1, area.height() - 24)))
        except Exception:
            logger.exception("No se pudo ajustar la configuración al tamaño de pantalla.")
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel("Tu panel de EON")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        card = QFrame()
        card.setObjectName("card")
        form = QVBoxLayout(card)
        form.setContentsMargins(12, 12, 12, 12)
        settings = config.get_notch_settings()
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(QLabel("Ocultar en reposo"), 1)
        self.enabled = ToggleSwitch()
        self.enabled.setChecked(settings["notch_auto_hide_enabled"])
        toggle_row.addWidget(self.enabled)
        form.addLayout(toggle_row)
        seconds_row = QHBoxLayout()
        seconds_row.addWidget(QLabel("Tiempo de espera"), 1)
        self.presets = QComboBox()
        for value in (10, 15, 20):
            self.presets.addItem(f"{value} s", value)
        self.presets.addItem("Personalizado", None)
        self.presets.setAccessibleName("Tiempo de espera en segundos")
        self.seconds = QSpinBox()
        self.seconds.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.seconds.setRange(1, 2147483647)
        self.seconds.setValue(settings["notch_auto_hide_seconds"])
        self.seconds.setSuffix(" s")
        self.seconds.setAccessibleName("Tiempo personalizado en segundos")
        self.presets.currentIndexChanged.connect(self._preset_changed)
        self.seconds.valueChanged.connect(self._seconds_changed)
        seconds_row.addWidget(self.presets)
        seconds_row.addWidget(self.seconds)
        form.addLayout(seconds_row)
        self._seconds_changed(self.seconds.value())
        layout.addWidget(card)
        row = QHBoxLayout()
        row.addWidget(QLabel("Accesos directos"), 1)
        add = QPushButton("Añadir acceso")
        add.clicked.connect(self.add_shortcut)
        row.addWidget(add)
        layout.addLayout(row)
        self.cards: list[ShortcutCard] = []
        self._selected_card: ShortcutCard | None = None
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()
        scroll.setWidget(container)
        for shortcut in config.get_quick_launch_shortcuts():
            self._append_row(shortcut)
        layout.addWidget(scroll, 1)
        self.error = QLabel()
        self.error.setStyleSheet("color: #ffb3b3;")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append_row(self, shortcut: dict[str, str]) -> None:
        card = ShortcutCard(self, shortcut)
        self.cards.append(card)
        self._cards_layout.insertWidget(len(self.cards) - 1, card)
        self._selected_card = card

    def _preset_changed(self, index: int) -> None:
        value = self.presets.itemData(index)
        self.seconds.setVisible(value is None)
        if value is not None:
            self.seconds.setValue(value)

    def _seconds_changed(self, value: int) -> None:
        index = self.presets.findData(value)
        self.presets.blockSignals(True)
        self.presets.setCurrentIndex(index if index >= 0 else 3)
        self.presets.blockSignals(False)
        self.seconds.setVisible(index < 0)

    def add_shortcut(self) -> None:
        """Insert an editable row; saving an incomplete row fails visibly."""
        self._append_row({"label": "", "path": "", "color": "#8ea9c7"})

    def remove_shortcut(self) -> None:
        """Remove only the card whose action was selected."""
        card = self._selected_card
        if card in self.cards:
            self.cards.remove(card)
            self._cards_layout.removeWidget(card)
            card.deleteLater()
            self._selected_card = None

    def choose_file(self) -> None:
        """Use Qt's native file chooser, with guarded peripheral interaction."""
        self._choose_path(False)

    def choose_directory(self) -> None:
        """Let a shortcut also target a directory rather than an application."""
        self._choose_path(True)

    def _choose_path(self, directory: bool) -> None:
        try:
            if self._selected_card is None:
                self.add_shortcut()
            card = self._selected_card
            path = (QFileDialog.getExistingDirectory(self, "Elegir carpeta") if directory else
                    QFileDialog.getOpenFileName(self, "Elegir aplicación o archivo")[0])
            if path:
                card.path = path
                if not card.label.text():
                    card.label.setText(Path(path).stem)
                card._refresh_data()
        except Exception:
            logger.exception("No se pudo seleccionar la ruta.")
            self.error.setText("No se pudo seleccionar la ruta. Inténtalo de nuevo.")

    def choose_color(self) -> None:
        try:
            card = self._selected_card
            if card is None:
                self.error.setText("Selecciona primero un acceso directo.")
                return
            color = QColorDialog.getColor(QColor(card.color), self, "Elegir color")
            if color.isValid():
                card.color = color.name()
                card._refresh_data()
        except Exception:
            logger.exception("No se pudo seleccionar el color.")
            self.error.setText("No se pudo seleccionar el color.")

    def save(self) -> None:
        """Keep the dialog open with a Spanish explanation when saving fails."""
        try:
            shortcuts = [card.data() for card in self.cards]
            config.save_notch_settings(auto_hide_enabled=self.enabled.isChecked(),
                                       auto_hide_seconds=self.seconds.value(), shortcuts=shortcuts)
            self.saved.emit()
            self.accept()
        except Exception as exc:
            logger.exception("No se pudo guardar la configuración del panel.")
            self.error.setText(f"No se pudo guardar: {exc}")


class MicrophoneLevelWorker(QThread):
    """Poll real RMS off the GUI thread; never load STT, TTS or brain models."""

    level = pyqtSignal(float)
    result = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, *, duration: float | None = None,
                 detector_factory: Callable[[], AcousticDetector] | None = None) -> None:
        super().__init__(parent)
        if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0):
            raise ValueError("La duración del medidor debe ser positiva y finita.")
        self.duration = duration
        self.last_error: str | None = None
        self._detector_factory = detector_factory
        self._cancel = threading.Event()

    def stop(self) -> None:
        """Request bounded cancellation between short capture windows."""
        self._cancel.set()

    def run(self) -> None:
        try:
            if self._detector_factory is None:
                from core.acoustic_detector import AcousticDetector
                detector = AcousticDetector()
            else:
                detector = self._detector_factory()
            deadline = time.monotonic() + self.duration if self.duration is not None else None
            while not self._cancel.is_set() and (deadline is None or time.monotonic() < deadline):
                seconds = min(0.12, max(0.01, deadline - time.monotonic())) if deadline is not None else 0.12
                rms = detector.get_input_level(seconds)
                if detector.last_error:
                    raise RuntimeError(detector.last_error)
                if not math.isfinite(rms) or not 0 <= rms <= 1:
                    raise ValueError("El micrófono devolvió un nivel inválido.")
                self.level.emit(rms)
            self.result.emit("Medición cancelada." if self._cancel.is_set() else
                             "Medición terminada. No se ha transcrito ni guardado el audio.")
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("No se pudo medir el nivel del micrófono.")
            self.result.emit("No se pudo medir el micrófono. Revisa el dispositivo y sus permisos.")
