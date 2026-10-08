"""Render an edge-anchored state wave and honest, functional local controls."""

import logging
import math

from PyQt6.QtCore import (QEasingCurve, QEvent, QParallelAnimationGroup,
                         QPoint, QPropertyAnimation, QRect, QRectF, QSizeF, Qt, QTimer, pyqtProperty, pyqtSignal)
from PyQt6.QtGui import (QColor, QCloseEvent, QEnterEvent, QKeyEvent, QMouseEvent,
                        QLinearGradient, QPaintEvent, QPainter, QPainterPath, QRadialGradient, QScreen)
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                            QLabel, QLineEdit, QPushButton, QScrollArea,
                            QVBoxLayout, QWidget)

import config
from core.eon_state import EonState, STATE_LABELS, state_color
from gui.char_widget import CharWidget
from gui.notch_controls import (EON_STYLE, IconButton, MicrophoneLevelWorker, PeakLevelBar,
                                SettingsDialog, SpeechBubble, launch_shortcut)
from gui.microphone_meter import MicrophoneMeter
from gui.notch_window import NotchController, NotchGeometryState

logger = logging.getLogger(__name__)
PANEL_SIZES = {NotchGeometryState.PEEK: (300, 16), NotchGeometryState.HOVER_PEEK: (300, 27),
               NotchGeometryState.EXPANDED: (680, 300)}
PANEL_PADDING = 18
EXPAND_DURATION_MS = 330
COLLAPSE_DURATION_MS = 290
WAVE_DURATION_MS = 620
TICK_INTERVAL_MS = 200
CAPSULE_COLOR = "#121318"
REVEAL_LEVELS = {NotchGeometryState.PEEK: 0.0, NotchGeometryState.HOVER_PEEK: 0.0,
                 NotchGeometryState.EXPANDED: 1.0}
ACKNOWLEDGEMENT = "Mensaje recibido. La conexión con el cerebro de EON se activará en una fase posterior."


def panel_size_for_screen(state: NotchGeometryState, width: int, height: int) -> QSizeF:
    """Scale resting width gently and clamp every dimension to the real screen."""
    desired_width, desired_height = PANEL_SIZES[state]
    if state != NotchGeometryState.EXPANDED:
        desired_width = max(280, min(320, round(300 * width / 1920)))
    return QSizeF(min(desired_width, max(1, width - 24)), min(desired_height, max(1, height)))


class NotchWindow(QWidget):
    """Adapt pure state rules; microphone capture is opt-in and never performs AI."""

    closed = pyqtSignal()

    def __init__(self, controller: NotchController | None = None) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.controller = controller if controller is not None else NotchController()
        self.last_error: str | None = None
        self._screen: QScreen | None = None
        self._screen_area = QRect(0, 0, 1920, 1080)
        self._visual_geometry = self.controller.geometry_state
        self._reveal = REVEAL_LEVELS[self._visual_geometry]
        self._opacity = self._reveal
        self._size = QSizeF(*PANEL_SIZES[self._visual_geometry])
        self._wave_state = self.controller.eon_state
        self._wave_progress = 1.0
        self._wave_base = self._state_tint(self._wave_state)
        self._wave_target = QColor(self._wave_base)
        self._shape_mix = 1.0 if self._visual_geometry == NotchGeometryState.EXPANDED else 0.0
        self._meter_model = MicrophoneMeter()
        self._microphone: MicrophoneLevelWorker | None = None
        self._voice_before_meter = False
        self._close_pending = False
        self._closed = False
        self._settings_dialog: SettingsDialog | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Eon — Notch")
        self.setAccessibleName("Panel de estado de Eon")
        self.setStyleSheet(EON_STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        try:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception as exc:
            self._report_error("No se pudo configurar el cursor del notch", exc)
        self._build_content()
        self._geometry_animation = QParallelAnimationGroup(self)
        self._size_animation = QPropertyAnimation(self, b"panel_size")
        self._opacity_animation = QPropertyAnimation(self, b"content_opacity")
        self._reveal_animation = QPropertyAnimation(self, b"reveal_progress")
        for animation in (self._size_animation, self._opacity_animation, self._reveal_animation):
            self._geometry_animation.addAnimation(animation)
        self._wave_animation = QPropertyAnimation(self, b"wave_progress", self)
        self._wave_animation.setDuration(WAVE_DURATION_MS)
        self._wave_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._shape_animation = QPropertyAnimation(self, b"shape_mix", self)
        self._shape_animation.setDuration(180)
        self._shape_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(40)
        self._meter_timer.timeout.connect(self._refresh_peak)
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.setInterval(1600)
        self._bubble_timer.timeout.connect(self._character_bubble.hide)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        try:
            QApplication.instance().primaryScreenChanged.connect(self._on_primary_screen_changed)
        except Exception as exc:
            self._report_error("No se pudo observar la pantalla principal", exc)
        self._read_screen()
        self._set_panel_size(self._target_size())
        self.refresh()
        self._timer.start()

    def _build_content(self) -> None:
        self._content = QWidget(self)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header_widget = QWidget()
        header_widget.setFixedHeight(76)
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        self.character = CharWidget(self._content)
        self.character.setFixedSize(120, 76)
        self.character.clicked.connect(self._character_clicked)
        header.addWidget(self.character)
        surface = QFrame()
        surface.setStyleSheet("QFrame {background: rgba(8, 10, 16, 210); border-radius: 9px;}"
                             "QLabel {color: #f0f2f6; background: transparent;}")
        text_layout = QVBoxLayout(surface)
        text_layout.setContentsMargins(12, 8, 12, 8)
        text_layout.setSpacing(2)
        self._title = QLabel("Eon")
        self._title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self._status = QLabel()
        self._status.setStyleSheet("font-size: 13px;")
        text_layout.addWidget(self._title)
        text_layout.addWidget(self._status)
        header.addWidget(surface, 1)
        self.settings_button = IconButton("gear", "Configurar notch", self._content)
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)
        self._character_bubble = SpeechBubble(header_widget)
        self._character_bubble.hide()
        self._bubble_index = 0
        layout.addWidget(header_widget)
        self._shortcuts_scroll = QScrollArea()
        self._shortcuts_scroll.setWidgetResizable(True)
        self._shortcuts_scroll.viewport().setAutoFillBackground(False)
        self._shortcuts_scroll.setFixedHeight(46)
        self._shortcuts_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._shortcuts_scroll.setStyleSheet("QScrollArea {background: transparent; border: 0;}"
                                            "QScrollBar:horizontal {height: 6px;}")
        layout.addWidget(self._shortcuts_scroll)
        self._message = QLabel("Entrada local de texto; la respuesta de IA aún no está conectada.")
        self._message.setWordWrap(True)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setFixedHeight(48)
        self._message.setStyleSheet("color: #f0f2f6; font-size: 12px; background: rgba(8, 10, 16, 210);"
                                   "border-radius: 8px; padding: 7px;")
        layout.addWidget(self._message)
        meter_row = QHBoxLayout()
        meter_row.setSpacing(12)
        self._meter_status = QLabel("Micrófono inactivo")
        self._meter_status.setStyleSheet("color: #b9c1d0; font-size: 11px;")
        self._meter_status.setFixedWidth(138)
        self._meter_status.setFixedHeight(18)
        meter_row.addWidget(self._meter_status)
        self._meter = PeakLevelBar()
        self._meter.setValue(0)
        meter_row.addWidget(self._meter, 1)
        layout.addLayout(meter_row)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.text_input = QLineEdit()
        self.text_input.setAccessibleName("Mensaje para Eon")
        self.text_input.setToolTip("Escribe un mensaje y pulsa Enter o Enviar")
        self.text_input.setMinimumWidth(1)
        self.text_input.setFixedHeight(44)
        self.text_input.setStyleSheet("QLineEdit {color: #f0f2f6; background: rgba(8, 10, 16, 230);"
                                     "border: 1px solid #424654; border-radius: 10px; padding: 0 10px;}"
                                     "QLineEdit:focus {border: 1px solid #f0f2f6;}")
        self.text_input.textChanged.connect(self._draft_changed)
        self.text_input.returnPressed.connect(self._send_input)
        row.addWidget(self.text_input, 1)
        self.microphone_button = IconButton("microphone", "Activar o detener el medidor de micrófono")
        self.microphone_button.setCheckable(True)
        self.microphone_button.clicked.connect(self.start_microphone_meter)
        row.addWidget(self.microphone_button)
        self.send_button = IconButton("send", "Enviar mensaje")
        self.send_button.clicked.connect(self._send_input)
        row.addWidget(self.send_button)
        layout.addLayout(row)
        self._content_effect = QGraphicsOpacityEffect(self._content)
        self._content.setGraphicsEffect(self._content_effect)
        self._label_effects = [self._content_effect]
        self._rebuild_shortcuts()

    def _character_clicked(self) -> None:
        phrases = ("Aquí sigo.", "Te escucho.", "Estoy aquí.")
        self._character_bubble.setText(phrases[self._bubble_index % len(phrases)])
        self._bubble_index += 1
        self._character_bubble.adjustSize()
        title_position = self._title.mapTo(self._character_bubble.parentWidget(), QPoint(0, 0))
        self._character_bubble.move(title_position.x() + self._title.fontMetrics().horizontalAdvance("Eon") + 12,
                                    max(0, title_position.y() - 6))
        self._character_bubble.show()
        self._character_bubble.raise_()
        self._bubble_timer.start()

    def _rebuild_shortcuts(self) -> None:
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        container.setAutoFillBackground(False)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.shortcut_buttons: list[QPushButton] = []
        for shortcut in config.get_quick_launch_shortcuts():
            button = QPushButton(shortcut["label"])
            button.setFixedHeight(36)
            button.setToolTip(shortcut["path"])
            try:
                button.setCursor(Qt.CursorShape.PointingHandCursor)
            except Exception:
                logger.exception("No se pudo configurar el cursor del acceso directo.")
            button.setStyleSheet("QPushButton {color: #f0f2f6; background: rgba(8, 10, 16, 220);"
                                f"border: 1px solid {shortcut['color']}; border-radius: 8px; padding: 0 12px;}}"
                                "QPushButton:hover {background: #292d37;}"
                                "QPushButton:focus {border: 2px solid #f0f2f6;}")
            button.clicked.connect(lambda checked=False, path=shortcut["path"]: self._launch(path))
            row.addWidget(button)
            self.shortcut_buttons.append(button)
        row.addStretch()
        old = self._shortcuts_scroll.takeWidget()
        self._shortcuts_scroll.setWidget(container)
        if old is not None:
            old.deleteLater()
        self._shortcuts_scroll.setVisible(bool(self.shortcut_buttons))
        order = [self.character, self.settings_button, *self.shortcut_buttons, self.text_input,
                 self.microphone_button, self.send_button]
        for first, second in zip(order, order[1:]):
            QWidget.setTabOrder(first, second)

    def _launch(self, path: str) -> None:
        _, message = launch_shortcut(path)
        self._message.setText(message)

    def handle_user_message(self, text: str) -> None:
        """Acknowledge nonempty text honestly, without inference or storing its content."""
        if not isinstance(text, str):
            raise ValueError("El mensaje debe ser texto.")
        if not text.strip():
            return
        self.text_input.clear()
        self._message.setText(ACKNOWLEDGEMENT)

    def _send_input(self) -> None:
        self.handle_user_message(self.text_input.text())

    def _draft_changed(self, text: str) -> None:
        self.controller.notify_draft_activity(bool(text.strip()))

    def open_settings(self) -> None:
        """Open one real nonmodal dialog; keep the notch open while it is in use."""
        try:
            if self._settings_dialog is not None:
                self._settings_dialog.raise_()
                self._settings_dialog.activateWindow()
                return
            self.controller.expand()
            dialog = SettingsDialog(self)
            self._settings_dialog = dialog
            dialog.saved.connect(self._apply_settings)
            dialog.finished.connect(self._settings_closed)
            dialog.show()
        except Exception as exc:
            self._report_error("No se pudo abrir la configuración", exc)
            self._message.setText("No se pudo abrir la configuración.")

    def _apply_settings(self) -> None:
        try:
            self.controller.reload_settings()
            self._rebuild_shortcuts()
            self._message.setText("Configuración guardada y aplicada.")
            self.refresh()
        except Exception as exc:
            self._report_error("No se pudo aplicar la configuración guardada", exc)
            self._message.setText("Ajustes guardados; no se pudieron aplicar al panel. Reinicia la prueba.")

    def _settings_closed(self, result: int) -> None:
        dialog, self._settings_dialog = self._settings_dialog, None
        if dialog is not None:
            dialog.deleteLater()
        self.controller.on_mouse_leave()
        self.refresh()

    def start_microphone_meter(self) -> None:
        """Start at most one worker; leave every GUI update on the main thread."""
        if self._microphone is not None:
            self._microphone.stop()
            self.microphone_button.setChecked(False)
            self._meter_status.setText("Deteniendo…")
            return
        self._voice_before_meter = self.controller.voice_active
        try:
            worker = MicrophoneLevelWorker(self)
            self._microphone = worker
            self.controller.notify_voice_activity(True)
            self.microphone_button.setChecked(True)
            self._meter_model.reset()
            self._meter.setValue(0)
            self._meter_status.setText("Esperando señal…")
            self._message.setText("Medidor activo. Pulsa de nuevo el micrófono para detenerlo; sin transcripción.")
            worker.level.connect(self._update_level)
            worker.result.connect(self._meter_result)
            worker.finished.connect(self._microphone_finished)
            worker.start()
            self._meter_timer.start()
        except Exception as exc:
            self._report_error("No se pudo iniciar el medidor del micrófono", exc)
            self._message.setText("No se pudo iniciar el micrófono.")
            self._microphone_finished()
            self._meter_status.setText("Error de micrófono")

    def _update_level(self, rms: float) -> None:
        try:
            level = self._meter_model.update(rms)
            self._meter.setValue(round(level * 1000))
            self._refresh_peak()
            db = 20 * math.log10(max(rms, 1e-6))
            self._meter_status.setText(f"Señal · {db:.0f} dB" if level > 0 else "Silencio")
            self._meter.setAccessibleDescription(f"RMS: {rms:.5f}; {db:.1f} dB; nivel: {level:.0%}")
        except Exception as exc:
            self._report_error("No se pudo representar el nivel del micrófono", exc)
            self._meter_status.setText("Error de micrófono")

    def _refresh_peak(self) -> None:
        self._meter.peak = self._meter_model.tick()
        self._meter.update()

    def _meter_result(self, message: str) -> None:
        self._message.setText(message)
        if self._microphone is not None and self._microphone.last_error is not None:
            self._meter_status.setText("Error de micrófono")
            self._meter.setAccessibleDescription("Error de captura; no es silencio.")

    def _microphone_finished(self) -> None:
        worker, self._microphone = self._microphone, None
        if worker is not None:
            worker.deleteLater()
        self.controller.notify_voice_activity(self._voice_before_meter)
        self.microphone_button.setEnabled(True)
        self.microphone_button.setChecked(False)
        self._meter_timer.stop()
        self._meter_model.reset()
        self._meter.peak = 0
        self._meter.setValue(0)
        if worker is None or worker.last_error is None:
            self._meter_status.setText("Micrófono inactivo")
        if self._close_pending:
            self.close()

    def _get_panel_size(self) -> QSizeF:
        return QSizeF(self._size)

    def _set_panel_size(self, size: QSizeF) -> None:
        try:
            area = self._screen_area
            width = min(area.width(), max(1, round(size.width())))
            height = min(area.height(), max(1, round(size.height())))
            self._size = QSizeF(width, height)
            self.resize(width, height)
            self.move(area.x() + (area.width() - width) // 2, area.y())
            self._apply_reveal_frame()
        except Exception as exc:
            self._report_error("No se pudo redimensionar la ventana", exc)

    panel_size = pyqtProperty(QSizeF, fget=_get_panel_size, fset=_set_panel_size)

    def _get_reveal_progress(self) -> float:
        return self._reveal

    def _set_reveal_progress(self, progress: float) -> None:
        self._reveal = max(0.0, min(1.0, progress)) if self.controller.geometry_state == NotchGeometryState.EXPANDED else 0.0
        self._apply_reveal_frame()

    reveal_progress = pyqtProperty(float, fget=_get_reveal_progress, fset=_set_reveal_progress)

    def _get_content_opacity(self) -> float:
        return self._opacity

    def _set_content_opacity(self, opacity: float) -> None:
        self._opacity = max(0.0, min(1.0, opacity))
        self._content_effect.setOpacity(self._opacity)

    content_opacity = pyqtProperty(float, fget=_get_content_opacity, fset=_set_content_opacity)

    def _apply_reveal_frame(self) -> None:
        if not hasattr(self, "_content"):
            return
        final = panel_size_for_screen(NotchGeometryState.EXPANDED, self._screen_area.width(), self._screen_area.height())
        self._content.setGeometry(PANEL_PADDING, PANEL_PADDING + round(10 * (1 - self._reveal)),
                                  max(1, round(final.width()) - 2 * PANEL_PADDING),
                                  max(1, round(final.height()) - 2 * PANEL_PADDING))
        expanded = self.controller.geometry_state == NotchGeometryState.EXPANDED
        self._content.setVisible(expanded)
        self.character.setVisible(expanded)
        if not expanded:
            self._character_bubble.hide()
        self._content_effect.setOpacity(self._opacity)

    @staticmethod
    def _state_tint(state: EonState) -> QColor:
        base, tint = QColor(CAPSULE_COLOR), QColor(state_color(state))
        weight = 0.035 if state == EonState.IDLE else 0.23
        return QColor.fromRgbF(*(a + (b - a) * weight for a, b in zip(base.getRgbF(), tint.getRgbF())))

    def _get_wave_progress(self) -> float:
        return self._wave_progress

    def _set_wave_progress(self, progress: float) -> None:
        self._wave_progress = max(0.0, min(1.0, progress))
        self.update()

    wave_progress = pyqtProperty(float, fget=_get_wave_progress, fset=_set_wave_progress)

    def _get_shape_mix(self) -> float:
        return self._shape_mix

    def _set_shape_mix(self, progress: float) -> None:
        self._shape_mix = max(0.0, min(1.0, progress))
        self.update()

    shape_mix = pyqtProperty(float, fget=_get_shape_mix, fset=_set_shape_mix)

    def _start_state_wave(self) -> None:
        state = self.controller.eon_state
        if state == self._wave_state:
            return
        self._wave_animation.stop()
        progress = self._wave_progress
        self._wave_base = QColor.fromRgbF(*(a + (b - a) * progress for a, b in
                                            zip(self._wave_base.getRgbF(), self._wave_target.getRgbF())))
        self._wave_target = self._state_tint(state)
        self._wave_state = state
        self._wave_animation.setStartValue(0.0)
        self._wave_animation.setEndValue(1.0)
        self._wave_animation.start()

    def _report_error(self, operation: str, exc: Exception) -> None:
        self.last_error = f"{operation}: {exc}"
        logger.exception("%s.", operation)

    def _on_primary_screen_changed(self, screen: QScreen | None) -> None:
        self.refresh()

    def _read_screen(self) -> bool:
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
            self._screen_area = area
            self.last_error = None
            return True
        except Exception as exc:
            self._report_error("No se pudo posicionar el notch en la pantalla", exc)
            return False

    def _target_size(self) -> QSizeF:
        return panel_size_for_screen(self.controller.geometry_state, self._screen_area.width(), self._screen_area.height())

    def _animate_geometry(self, target: QSizeF) -> None:
        self._geometry_animation.stop()
        expanded = self.controller.geometry_state == NotchGeometryState.EXPANDED
        self._shape_animation.stop()
        self._shape_animation.setStartValue(self._shape_mix)
        self._shape_animation.setEndValue(1.0 if expanded else 0.0)
        self._shape_animation.start()
        duration = EXPAND_DURATION_MS if expanded else COLLAPSE_DURATION_MS
        curve = QEasingCurve(QEasingCurve.Type.OutBack if expanded else QEasingCurve.Type.OutQuad)
        if expanded:
            curve.setOvershoot(0.55)
        self._size_animation.setEasingCurve(curve)
        for animation, start, end in (
            (self._size_animation, self.panel_size, target),
            (self._opacity_animation, self._opacity, 1.0 if expanded else 0.0),
            (self._reveal_animation, self._reveal, 1.0 if expanded else 0.0),
        ):
            animation.setDuration(duration)
            if animation is not self._size_animation:
                animation.setEasingCurve(QEasingCurve.Type.OutCubic if expanded else QEasingCurve.Type.OutQuad)
            animation.setStartValue(start)
            animation.setEndValue(end)
        self._geometry_animation.start()

    def refresh(self, *args: object) -> None:
        """Advance pure logic and render independent geometry and state-color tracks."""
        try:
            if self._settings_dialog is not None:
                self.controller.on_mouse_enter()
            self.controller.tick()
            expanded = self.controller.geometry_state == NotchGeometryState.EXPANDED
            if bool(self.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus) == expanded:
                was_visible = self.isVisible()
                self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, not expanded)
                self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if expanded else Qt.FocusPolicy.NoFocus)
                if was_visible:
                    self.show()
            self.character.set_eon_state(self.controller.eon_state)
            self._start_state_wave()
            label = STATE_LABELS[self.controller.eon_state]
            self._status.setText(label)
            self.setAccessibleDescription(label)
            if self._read_screen():
                target = self._target_size()
                if self._visual_geometry != self.controller.geometry_state:
                    self._visual_geometry = self.controller.geometry_state
                    if not expanded:
                        self._reveal = 0.0
                    self._animate_geometry(target)
                elif self._size_animation.endValue() != target and self._geometry_animation.state() == QParallelAnimationGroup.State.Running:
                    self._animate_geometry(target)
                elif self._geometry_animation.state() != QParallelAnimationGroup.State.Running:
                    self._set_panel_size(target)
                else:
                    self._set_panel_size(self.panel_size)
            self._apply_reveal_frame()
            self.update()
        except Exception as exc:
            self._report_error("No se pudo actualizar el notch", exc)

    def enterEvent(self, event: QEnterEvent) -> None:
        try:
            self.controller.on_mouse_enter()
            self.refresh()
        except Exception as exc:
            self._report_error("No se pudo procesar la entrada del ratón", exc)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        try:
            if self._settings_dialog is None:
                self.controller.on_mouse_leave()
            self.refresh()
        except Exception as exc:
            self._report_error("No se pudo procesar la salida del ratón", exc)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
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
        """Defer close until a running audio thread has released the device."""
        if self._closed:
            super().closeEvent(event)
            return
        if self._microphone is not None:
            self._close_pending = True
            self._microphone.stop()
            event.ignore()
            return
        self._timer.stop()
        self._geometry_animation.stop()
        self._wave_animation.stop()
        self._shape_animation.stop()
        self._bubble_timer.stop()
        self._meter_timer.stop()
        self.character.hide()
        if self._settings_dialog is not None:
            self._settings_dialog.reject()
        try:
            QApplication.instance().primaryScreenChanged.disconnect(self._on_primary_screen_changed)
            if self._screen is not None:
                self._screen.geometryChanged.disconnect(self.refresh)
        except (TypeError, RuntimeError, AttributeError) as exc:
            self._report_error("No se pudieron liberar los observadores de pantalla", exc)
        super().closeEvent(event)
        self._closed = True
        self.closed.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            right, bottom = float(self.width()), float(self.height())
            radius = min(22.0, bottom / 2)
            capsule = QPainterPath()
            capsule.moveTo(0, 0)
            capsule.lineTo(right, 0)
            capsule.lineTo(right, bottom - radius)
            capsule.quadTo(right, bottom, right - radius, bottom)
            capsule.lineTo(radius, bottom)
            capsule.quadTo(0, bottom, 0, bottom - radius)
            capsule.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(capsule, QColor(CAPSULE_COLOR))
            painter.setClipPath(capsule)
            self._paint_state_wave(painter, right, bottom, radial=True)
            if self._shape_mix < 1:
                painter.save()
                painter.setOpacity(1 - self._shape_mix)
                self._paint_state_wave(painter, right, bottom, radial=False)
                painter.restore()
            if self.hasFocus() and self.controller.geometry_state == NotchGeometryState.EXPANDED:
                painter.setPen(QColor("#f0f2f6"))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(capsule)
        finally:
            painter.end()

    def _paint_state_wave(self, painter: QPainter, width: float, height: float, *, radial: bool) -> None:
        """Mirror horizontal stops in closed modes; retain the elliptical radial wave."""
        painter.save()
        painter.translate(width / 2, height * 0.40)
        painter.scale(width / 2, max(1.0, height * 0.8))

        def gradient(radius: float, stops: list[tuple[float, QColor]]) -> QRadialGradient | QLinearGradient:
            radius = max(0.001, radius)
            result = QRadialGradient(0, 0, radius) if radial else QLinearGradient(-radius, 0, radius, 0)
            for position, color in stops:
                if radial:
                    result.setColorAt(position, color)
                else:
                    result.setColorAt((1 - position) / 2, color)
                    result.setColorAt((1 + position) / 2, color)
            return result

        area = QRectF(-1, -1, 2, 2)
        progress = self._wave_progress
        base = self._wave_target if progress == 1 else self._wave_base
        painter.fillRect(area, gradient(1, [(0, base), (1, QColor(CAPSULE_COLOR))]))
        tint = QColor(self._wave_target)
        tint.setAlphaF(min(1.0, progress * 8))
        clear = QColor(tint)
        clear.setAlpha(0)
        painter.fillRect(area, gradient(progress, [(0, tint), (1, clear)]))
        if 0 < progress < 1:
            color = QColor(state_color(self._wave_state))
            color.setAlpha(round(24 * math.sin(math.pi * progress)))
            painter.fillRect(area, gradient(progress, [(0, clear), (0.7, clear), (0.86, color), (1, clear)]))
        painter.restore()
