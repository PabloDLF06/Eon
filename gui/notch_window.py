"""Ventana flotante del Dynamic Notch (spec 2.1).

Una cápsula negra anclada al borde superior del monitor primario: sin bordes,
siempre encima, click-through mientras EON está pasivo, y con la interacción
activada al pasar el ratón o al desplegar una tarjeta. La morfología entre
estados la anima ``QPropertyAnimation`` (OutBack al abrir, OutCubic al cerrar);
el contenido se pinta desde ``gui.notch_layout``, que en cada fotograma usa la
geometría **real** de la ventana, de modo que forma y contenido no pueden
desincronizarse aunque el compositor se atrase.

Plataforma:
* Windows: se tocan ``WS_EX_TRANSPARENT`` / ``WS_EX_NOACTIVATE`` / ``WS_EX_TOOLWINDOW``
  con ``ctypes.windll.user32`` para el click-through nativo (spec 2.1); el
  layering lo gestiona Qt (``WA_TranslucentBackground``).
* Linux/macOS: se usa el equivalente de Qt (``WindowTransparentForInput``), así
  que el desarrollo en otro SO también funciona.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPainter
from PyQt6.QtWidgets import QWidget

from gui.char_kinematics import CharState
from gui.char_widget import CharWidget
from gui.notch_layout import (
    Geometry,
    MediaInfo,
    NotchController,
    NotchState,
    build_scene,
    capsule_corner,
    char_box_for,
    corner_params,
)
from gui.qt_paint import paint_scene

# --- estilos extendidos de Win32 (valores de la SDK) ----------------------- #
# OJO: los bits de layering/composited NO se tocan aquí: Qt gestiona el
# layering con WA_TranslucentBackground y fijarlos a mano deja la ventana con
# alfa 0 (isla invisible). El AST de apply_win32_styles se audita en tests/.
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_FRAMECHANGED = 0x0020

#: Aire alrededor de la cápsula para que el halo pueda desbordar sin recortarse.
_MARGIN_X = 26.0
_MARGIN_BOTTOM = 20.0
_TOP_PAD = 1.0


def apply_win32_styles(hwnd: int, transparent: bool) -> bool:
    """Ajusta los estilos extendidos de la ventana y la remata *topmost*.

    El layering lo gestiona Qt (``WA_TranslucentBackground``); tocar aquí los
    bits de layering/composited dejaría la ventana con alfa 0 (isla invisible).
    Por eso solo se ajustan ``WS_EX_TOOLWINDOW`` y ``WS_EX_NOACTIVATE`` y,
    según ``transparent``, ``WS_EX_TRANSPARENT`` (click-through nativo,
    spec 2.1). El estilo se reescribe únicamente si cambia y se remata con
    ``SetWindowPos(HWND_TOPMOST, SWP_NOSIZE|SWP_NOMOVE|SWP_FRAMECHANGED)``.

    Devuelve ``True`` si tocó algo. Jamás lanza: si ``user32`` no está (otro SO,
    ``ctypes`` recortado) la ventana conserva el comportamiento de Qt y EON sólo
    pierde el click-through nativo, nunca la funcionalidad.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        getter = getattr(user32, "GetWindowLongW", None) or getattr(user32, "GetWindowLongA", None)
        setter = getattr(user32, "SetWindowLongW", None) or getattr(user32, "SetWindowLongA", None)
        if not (getter and setter):  # pragma: no cover - entornos exóticos
            return False
        style = int(getter(int(hwnd), GWL_EXSTYLE))
        new_style = style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        new_style = (new_style | WS_EX_TRANSPARENT) if transparent else (new_style & ~WS_EX_TRANSPARENT)
        if new_style != style:  # reescribir el estilo únicamente si cambia
            setter(int(hwnd), GWL_EXSTYLE, new_style)
        # handles como c_void_p: HWND_TOPMOST=-1 se ensancha bien en 32 y 64 bits
        user32.SetWindowPos(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_void_p(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_FRAMECHANGED,
        )
        return True
    except (AttributeError, OSError):  # pragma: no cover
        return False


#: El pintado no puede abortar el proceso (en PyQt6 una excepción sin capturar
#: dentro de paintEvent termina en qFatal/abort: parpadeo y adiós). Se registra
#: una vez por ráfaga y la isla sigue viva mientras se busca el fallo.
_paint_errors: dict[str, float] = {}


def _log_paint_error(where: str, exc: Exception) -> None:
    now = time.monotonic()
    if now - _paint_errors.get(where, -60.0) > 30.0:
        _paint_errors[where] = now
        logging.getLogger("eon.gui").error(
            "fallo pintando %s (%s): %s — la isla seguirá viva; mira logs/eon.log", where, type(exc).__name__, exc, exc_info=exc
        )


class NotchWindow(QWidget):
    """La isla. Un único ejemplar por aplicación."""

    #: Nombre del estado al que se acaba de transicionar.
    stateChanged = pyqtSignal(str)
    #: El usuario pulsó la cápsula (escucha manual).
    activated = pyqtSignal()
    #: Texto aceptado desde el notch.
    commandSubmitted = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        controller: NotchController | None = None,
        screen_index: int = 0,
        fps: int = 60,
        embed_character: bool = True,
        allow_command_input: bool = True,
    ) -> None:
        super().__init__(parent)
        self._controller = controller or NotchController()
        self._screen_index = int(screen_index)
        self._allow_input = bool(allow_command_input)
        self._embed_character = bool(embed_character)
        self._click_through = True
        self._drag_origin: QPoint | None = None
        self._state_listeners: list[Callable[[str], None]] = []

        # flags pedidos en la spec + Tool para no ensuciar la barra de tareas
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SubWindow
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        for attribute in (
            Qt.WidgetAttribute.WA_TranslucentBackground,
            Qt.WidgetAttribute.WA_NoSystemBackground,
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
        ):
            self.setAttribute(attribute, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if allow_command_input else Qt.FocusPolicy.NoFocus)

        # El personaje va *dentro* de la escena del notch (un solo pintado).
        # Sólo se crea el widget hijo si se quiere el blob como ventana propia.
        self._char: CharWidget | None = None
        if not self._embed_character:
            self._char = CharWidget(self, model=self._controller.char, autoplay=False)
            self._char.show()

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._anim.valueChanged.connect(self._relayout_children)

        self._ticker = QTimer(self)
        self._ticker.setInterval(max(8, int(1000 / max(24, int(fps)))))
        self._ticker.timeout.connect(self._on_frame)
        self.stateChanged.connect(self._on_state_changed)

    # ------------------------------------------------------------------ API --
    @property
    def controller(self) -> NotchController:
        return self._controller

    @property
    def char_widget(self) -> CharWidget | None:
        return self._char

    def on_state(self, callback: Callable[[str], None]) -> None:
        """Oyentes de estado (glow de pantalla, motores de voz...)."""
        self._state_listeners.append(callback)

    def request(
        self,
        state: NotchState | str,
        status: str = "",
        detail: str = "",
        accent: str | None = None,
        animate: bool = True,
    ) -> NotchState:
        """Cambia de estado y anima la cápsula al tamaño objetivo."""
        target = NotchState.parse(state)
        changed = target is not self._controller.state
        if status or detail:
            self._controller.set_status(status, detail)
        if changed:
            self._controller.set_state(target, accent=accent)
        else:
            self._controller.note_activity()
        self._animate_to(animate=animate)
        if changed:
            self.stateChanged.emit(target.value)
        return target

    def set_media(self, info: MediaInfo) -> None:
        self._controller.set_media(info)
        if self._controller.state in (NotchState.MEDIA, NotchState.ACTION):
            self._animate_to()

    def clear_media(self) -> None:
        self._controller.set_media(MediaInfo())
        self._animate_to()

    def set_status(self, status: str, detail: str = "") -> None:
        self._controller.set_status(status, detail)
        self.update()

    def set_energy(self, level: float) -> None:
        """Nivel de audio (micrófono o salida TTS): barras ecualizadas y boca."""
        self._controller.char.speak_level(max(0.0, min(1.0, float(level))))

    def wake(self) -> None:
        self._controller.char.trigger_wake()
        self.update()

    def rest(self) -> None:
        """Reposo total: cierra tarjetas y colapsa la cápsula (kill switch)."""
        self._controller.collapse()
        self._animate_to()
        self.stateChanged.emit(NotchState.IDLE.value)

    def set_click_through(self, enabled: bool) -> None:
        """``True`` => el ratón atraviesa la ventana (isla pasiva)."""
        enabled = bool(enabled)
        if enabled == getattr(self, "_click_through", None):
            return
        self._click_through = enabled
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        if self.isVisible():
            # cambiar flags con la ventana visible la recrea: hay que re-mostrarla
            self.show()
            apply_win32_styles(int(self.winId()), enabled)

    # -------------------------------------------------------------- layout --
    def screen_rect(self) -> QRect:
        screens = QGuiApplication.screens()
        if not screens:  # pragma: no cover - sesión sin pantalla
            return QRect(0, 0, 1920, 1080)
        index = min(max(0, self._screen_index), len(screens) - 1)
        return screens[index].geometry()

    def capsule_rect(self, width: float, height: float) -> QRect:
        """Rect de ventana para una cápsula ``(w,h)`` pegada al borde superior."""
        screen = self.screen_rect()
        win_w = int(round(width + 2 * _MARGIN_X))
        win_h = int(round(height + _MARGIN_BOTTOM + _TOP_PAD))
        x = int(round(screen.x() + (screen.width() - win_w) / 2.0))
        return QRect(x, int(screen.y()), win_w, win_h)

    def _target_rect(self) -> QRect:
        snap = self._controller.snapshot()
        return self.capsule_rect(snap.geometry.width, snap.geometry.height)

    def _animate_to(self, animate: bool = True) -> None:
        target = self._target_rect()
        if self.isHidden():
            self.setGeometry(target)
            return
        if not animate or self.geometry() == target:
            self.setGeometry(target)
            self._relayout_children(self.geometry())
            return
        expanding = target.height() >= self.height()
        self._anim.stop()
        self._anim.setDuration(self._duration_for(expanding))
        self._anim.setEasingCurve(
            QEasingCurve.Type.OutBack if expanding else QEasingCurve.Type.OutCubic
        )
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    def _duration_for(self, expanding: bool) -> int:
        try:
            import config

            key = "expand" if expanding else "collapse"
            return int(config.NOTCH_ANIM_MS.get(key, 340))
        except Exception:  # pragma: no cover - config está siempre
            return 340

    def _relayout_children(self, _value: object = None) -> None:
        """Coloca el personaje (sólo si vive en su propio widget)."""
        if self._char is None:
            return
        snap = self._controller.snapshot()
        geo = snap.geometry
        left = _MARGIN_X + (self.width() - 2 * _MARGIN_X - geo.width) / 2.0
        inner = (left + 8.0, _TOP_PAD + 4.0, geo.width - 16.0, geo.height - 8.0)
        box = char_box_for(snap, inner)
        self._char.setGeometry(
            QRect(int(box[0]), int(box[1]), max(8, int(box[2])), max(8, int(box[3])))
        )
        self.update()

    # ------------------------------------------------------------- Qt hooks --
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setGeometry(self._target_rect())
        self._relayout_children(self.geometry())
        self._ticker.start()
        apply_win32_styles(int(self.winId()), self._click_through)

    def hideEvent(self, event) -> None:
        self._ticker.stop()
        super().hideEvent(event)

    def _on_frame(self) -> None:
        self._controller.tick(self._ticker.interval() / 1000.0)
        self.update()

    def _live_geometry(self) -> Geometry:
        """Geometría medida del lienzo, no la objetivo de la animación."""
        width = max(24.0, float(self.width()) - 2 * _MARGIN_X)
        height = max(18.0, float(self.height()) - _MARGIN_BOTTOM - _TOP_PAD)
        ratio, lo, hi = corner_params()
        return Geometry(width=width, height=height, corner=capsule_corner(width, height, ratio, lo, hi))

    def paintEvent(self, event) -> None:
        if self._embed_character:
            self._controller.char.update(self._ticker.interval() / 1000.0)
        painter = QPainter(self)
        try:
            snap = replace(self._controller.snapshot(), geometry=self._live_geometry())
            sc = build_scene(snap, screen_width=float(self.width()))
            paint_scene(painter, sc, offset=(-_MARGIN_X, 0.0))
        except Exception as exc:
            _log_paint_error("notch", exc)
        finally:
            painter.end()

    def enterEvent(self, event) -> None:
        self._controller.hover(True)
        self.set_click_through(False)
        self.update()

    def leaveEvent(self, event) -> None:
        if not self.hasFocus():
            self._controller.hover(False)
            self.set_click_through(True)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
            if self._allow_input and self._controller.state in (
                NotchState.MEDIA,
                NotchState.ACTION,
                NotchState.IDLE,
            ):
                self.setFocus(Qt.FocusPolicy.OtherFocusReason)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # En reposo la isla se puede arrastrar (monitores sin notch físico).
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._controller.state in (NotchState.IDLE, NotchState.SLEEPING)
        ):
            if self._drag_origin is None:
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            text = self._controller.status.strip()
            if text:
                self.commandSubmitted.emit(text)
            self._controller.set_status("")
            self.clearFocus()
        elif key == Qt.Key.Key_Escape:
            self.rest()
            self.clearFocus()
        elif key == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.activated.emit()
        else:
            super().keyPressEvent(event)

    def _on_state_changed(self, name: str) -> None:
        if self._char is not None:
            self._char.set_state(CharState.parse(name))
        for listener in list(self._state_listeners):
            try:
                listener(name)
            except Exception:  # pragma: no cover - un oyente roto no tumba la GUI
                continue


def notch_scene_for(window: NotchWindow) -> object:
    """Escena actual del notch: la usan los tests de GUI y la previsualización."""
    snap = replace(window.controller.snapshot(), geometry=window._live_geometry())
    return build_scene(snap, screen_width=float(max(window.width(), 240)))
