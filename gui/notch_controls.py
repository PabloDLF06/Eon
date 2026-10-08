"""Provide original painted controls, persisted settings and bounded audio metering."""

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

from PyQt6.QtCore import QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPaintEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
                            QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                            QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                            QWidget)

import config

if TYPE_CHECKING:
    from core.acoustic_detector import AcousticDetector

logger = logging.getLogger(__name__)


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
            painter.setBrush(QColor("#292d37" if self.underMouse() else "#1c1f28"))
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
        self.resize(640, 430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        settings = config.get_notch_settings()
        self.enabled = QCheckBox("Ocultar automáticamente en reposo")
        self.enabled.setChecked(settings["notch_auto_hide_enabled"])
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 2147483647)
        self.seconds.setValue(settings["notch_auto_hide_seconds"])
        self.seconds.setSuffix(" s")
        form.addRow(self.enabled)
        form.addRow("Tiempo de espera:", self.seconds)
        layout.addLayout(form)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Nombre", "Ruta absoluta", "Color #RRGGBB"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 285)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for shortcut in config.get_quick_launch_shortcuts():
            self._append_row(shortcut)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for label, callback in (("Añadir", self.add_shortcut), ("Eliminar", self.remove_shortcut),
                                ("Elegir archivo", self.choose_file), ("Elegir carpeta", self.choose_directory),
                                ("Elegir color", self.choose_color)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.error = QLabel()
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
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, field in enumerate(("label", "path", "color")):
            self.table.setItem(row, column, QTableWidgetItem(shortcut[field]))
        self.table.setCurrentCell(row, 0)

    def add_shortcut(self) -> None:
        """Insert an editable row; saving an incomplete row fails visibly."""
        self._append_row({"label": "", "path": "", "color": "#8ea9c7"})

    def remove_shortcut(self) -> None:
        """Remove selected rows without silently deleting a different shortcut."""
        for row in sorted({item.row() for item in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(row)

    def choose_file(self) -> None:
        """Use Qt's native file chooser, with guarded peripheral interaction."""
        self._choose_path(False)

    def choose_directory(self) -> None:
        """Let a shortcut also target a directory rather than an application."""
        self._choose_path(True)

    def _choose_path(self, directory: bool) -> None:
        try:
            row = self.table.currentRow()
            if row < 0:
                self.add_shortcut()
                row = self.table.currentRow()
            path = (QFileDialog.getExistingDirectory(self, "Elegir carpeta") if directory else
                    QFileDialog.getOpenFileName(self, "Elegir aplicación o archivo")[0])
            if path:
                self.table.item(row, 1).setText(path)
                if not self.table.item(row, 0).text():
                    self.table.item(row, 0).setText(Path(path).stem)
        except Exception:
            logger.exception("No se pudo seleccionar la ruta.")
            self.error.setText("No se pudo seleccionar la ruta. Inténtalo de nuevo.")

    def choose_color(self) -> None:
        try:
            row = self.table.currentRow()
            if row < 0:
                self.error.setText("Selecciona primero un acceso directo.")
                return
            color = QColorDialog.getColor(QColor(self.table.item(row, 2).text()), self, "Elegir color")
            if color.isValid():
                self.table.item(row, 2).setText(color.name())
        except Exception:
            logger.exception("No se pudo seleccionar el color.")
            self.error.setText("No se pudo seleccionar el color.")

    def save(self) -> None:
        """Keep the dialog open with a Spanish explanation when saving fails."""
        try:
            shortcuts = [{field: self.table.item(row, column).text()
                          for column, field in enumerate(("label", "path", "color"))}
                         for row in range(self.table.rowCount())]
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

    def __init__(self, parent: QWidget | None = None, *, duration: float = 3.0,
                 detector_factory: Callable[[], AcousticDetector] | None = None) -> None:
        super().__init__(parent)
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("La duración del medidor debe ser positiva y finita.")
        self.duration = duration
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
            deadline = time.monotonic() + self.duration
            while not self._cancel.is_set() and time.monotonic() < deadline:
                rms = detector.get_input_level(min(0.12, max(0.01, deadline - time.monotonic())))
                if detector.last_error:
                    raise RuntimeError(detector.last_error)
                if not math.isfinite(rms) or not 0 <= rms <= 1:
                    raise ValueError("El micrófono devolvió un nivel inválido.")
                self.level.emit(rms)
            self.result.emit("Medición cancelada." if self._cancel.is_set() else
                             "Medición terminada. No se ha transcrito ni guardado el audio.")
        except Exception:
            logger.exception("No se pudo medir el nivel del micrófono.")
            self.result.emit("No se pudo medir el micrófono. Revisa el dispositivo y sus permisos.")
