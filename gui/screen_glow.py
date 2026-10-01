"""Glow cianperimetérico de pantalla (spec 2.3).

Lienzo translúcido, sin bordes y click-through que cubre todo el escritorio
virtual. Se enciende cuando EON mira la pantalla (captura de ``mss``), cuando
envía el fotograma al modelo de visión o cuando actúa sobre el ratón/teclado,
para que Pablo vea *siempre* si la máquina está tomando el control.

Detalles de implementación:
* El relleno no usa ``QGraphicsBlurEffect`` (cuesta un backing-store completo a
  pantalla completa): el halo se logra apilando N trazos cada vez más gruesos y
  más transparentes, igual que en ``gui.scene.glow_strokes``.
* El fundido de entrada/salida es una ``QPropertyAnimation`` sobre una
  propiedad ``qreal`` del propio widget (150 ms dentro, 300 ms fuera).
* La geometría se recalcula al cambiar de resolución o al moverse la isla.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtProperty
from PyQt6.QtGui import QGuiApplication, QPainter
from PyQt6.QtWidgets import QWidget

from gui import scene
from gui.qt_paint import paint_scene
from gui.scene import Paint, Scene, Shape, Stroke

#: Razón -> (color, grosor relativo, pulso de escaneo)
MODES: dict[str, tuple[str, float, bool]] = {
    "capture": ("#00f0ff", 1.00, True),
    "vision": ("#00f0ff", 0.92, True),
    "act": ("#00e5ff", 0.86, False),
    "type": ("#7df9ff", 0.72, False),
    "alert": ("#ff6b8a", 1.10, False),
    "success": ("#4ade80", 0.90, False),
}


def glow_scene(
    width: float,
    height: float,
    intensity: float,
    mode: str = "capture",
    time_s: float = 0.0,
    scan: bool = True,
) -> Scene:
    """Construye la escena del borde luminoso (función pura, testeable).

    ``intensity`` 0..1 controla a la vez alfa y grosor: el neón de la spec va
    de 4 px a 12 px, y se consigue con ``stroke_base * (4 + 8*intensity)``.
    """
    intensity = scene.clamp01(intensity)
    color, weight, has_scan = MODES.get(mode, MODES["capture"])
    stroke_base = scene.lerp(4.0, 12.0, intensity) * weight
    inset = max(1.0, stroke_base * 0.62)
    rect = (inset, inset, max(2.0, width - 2 * inset), max(2.0, height - 2 * inset))
    x, y, w, h = rect
    radius = min(18.0, w * 0.02, h * 0.06)

    shapes: list[Shape] = []
    border = scene.rounded_rect_points(x, y, w, h, radius, radius, samples=10)
    shapes.append(
        Shape(
            points=tuple(border),
            stroke=Stroke(color=color, width=stroke_base, alpha=0.82 * intensity, glow=stroke_base * 1.35),
            closed=True,
            tag="border",
            z=0,
        )
    )
    # esquinas: cuatro escuadras más brillantes, marca de la casa
    bracket = min(64.0, w * 0.055)
    for cx, cy, sx, sy in (
        (x, y, 1, 1),
        (x + w, y, -1, 1),
        (x, y + h, 1, -1),
        (x + w, y + h, -1, -1),
    ):
        shapes.append(
            Shape(
                points=((cx + sx * bracket, cy), (cx, cy), (cx, cy + sy * bracket)),
                stroke=Stroke(color=color, width=stroke_base * 1.35 + 1.0, alpha=min(1.0, intensity * 1.15), glow=stroke_base),
                closed=False,
                tag="corner",
                z=2,
            )
        )
    # barrido vertical: la "mirada" de EON recorriendo la pantalla
    if scan and has_scan and intensity > 0.05:
        period = 1.35
        t = (time_s % period) / period
        line_y = y + h * t
        fade = math.sin(math.pi * t)
        shapes.append(
            Shape(
                points=((x + w * 0.02, line_y), (x + w * 0.98, line_y)),
                stroke=Stroke(color="#bffcff", width=1.8, alpha=0.55 * fade * intensity, glow=9.0),
                closed=False,
                tag="scan",
                z=3,
            )
        )
    # viñeta interior muy suave para que el borde "sude" luz dentro
    shapes.append(
        Shape(
            points=((x, y), (x + w, y), (x + w, y + h), (x, y + h)),
            fill=Paint(color=color, alpha=0.045 * intensity, radial=True, focal=(x + w / 2.0, y + h / 2.0)),
            closed=True,
            tag="vignette",
            z=-40,
        )
    )
    return Scene(width=width, height=height, shapes=tuple(shapes), meta={"mode": mode, "intensity": intensity})


class ScreenGlow(QWidget):
    """Capa a pantalla completa con el borde neón y su animación de opacidad."""

    def __init__(self, parent: QWidget | None = None, cover_all_screens: bool = True) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.SubWindow
        )
        for attribute in (
            Qt.WidgetAttribute.WA_TranslucentBackground,
            Qt.WidgetAttribute.WA_NoSystemBackground,
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
        ):
            self.setAttribute(attribute, True)
        self._opacity = 0.0
        self._mode = "capture"
        self._intensity = 1.0
        self._time = 0.0
        self._cover_all = bool(cover_all_screens)
        self._scan = True
        self._hold_target: float | None = None

        self._anim = QPropertyAnimation(self, b"glowOpacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._ticker = QTimer(self)
        self._ticker.setInterval(16)
        self._ticker.timeout.connect(self._on_frame)

        self._fade_in_ms, self._fade_out_ms = self._configured_fades()

    # ------------------------------------------------------------- propiedad --
    def _get_glow_opacity(self) -> float:
        return float(self._opacity)

    def _set_glow_opacity(self, value: float) -> None:
        self._opacity = float(value)
        self.update()

    glowOpacity = pyqtProperty(float, fget=_get_glow_opacity, fset=_set_glow_opacity)

    # ------------------------------------------------------------------ API --
    @staticmethod
    def _configured_fades() -> tuple[int, int]:
        try:
            import config

            return int(config.GLOW_FADE_IN_MS), int(config.GLOW_FADE_OUT_MS)
        except Exception:  # pragma: no cover - config siempre está
            return 150, 300

    def begin(self, mode: str = "capture", intensity: float = 1.0, scan: bool | None = None) -> None:
        """Enciende el glow y lo mantiene hasta :meth:`end`."""
        self._mode = mode if mode in MODES else "capture"
        self._intensity = scene.clamp01(intensity)
        if scan is not None:
            self._scan = bool(scan)
        self.resize_to_desktop()
        if not self.isVisible():
            self.show()
        self._animate_to(1.0, self._fade_in_ms)
        self._ticker.start()

    def end(self) -> None:
        """Apaga el glow con el fundido de la spec (300 ms)."""
        self._animate_to(0.0, self._fade_out_ms)

    def pulse(self, mode: str = "capture", duration_ms: int = 700, intensity: float = 1.0) -> None:
        """Destello de un disparo (captura puntual, clic, tecla pulsada)."""
        self._hold_target = max(0.0, duration_ms / 1000.0)
        self.begin(mode=mode, intensity=intensity)
        QTimer.singleShot(max(80, int(duration_ms)), self._release_pulse)

    def flash_alert(self, duration_ms: int = 1200) -> None:
        """Aviso rojo: se usa cuando una acción falla o el kill switch salta."""
        self.pulse("alert", duration_ms, intensity=1.0)

    def is_lit(self) -> bool:
        return self.isVisible() and self._opacity > 0.01

    def resize_to_desktop(self) -> None:
        """Cubre el escritorio virtual completo (multi-monitor incluido)."""
        try:
            if self._cover_all:
                geometry = QGuiApplication.primaryScreen().virtualGeometry()
            else:
                geometry = QGuiApplication.primaryScreen().geometry()
        except (AttributeError, RuntimeError):  # pragma: no cover - sin QGuiApplication
            geometry = None
        if geometry is not None:
            self.setGeometry(geometry)

    # ------------------------------------------------------------- Qt hooks --
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.resize_to_desktop()
        from gui.notch_window import apply_win32_styles

        apply_win32_styles(int(self.winId()), True)

    def _release_pulse(self) -> None:
        self._hold_target = None
        self.end()

    def _animate_to(self, target: float, duration_ms: int) -> None:
        self._anim.stop()
        self._anim.setDuration(max(40, int(duration_ms)))
        self._anim.setStartValue(self._opacity)
        self._anim.setEndValue(target)
        self._anim.finished.connect(self._on_anim_finished)
        self._anim.start()

    def _on_anim_finished(self) -> None:
        if self._opacity <= 0.001 and self._hold_target is None:
            self._ticker.stop()
            self.hide()

    def _on_frame(self) -> None:
        self._time += self._ticker.interval() / 1000.0
        if self._hold_target is not None:
            # durante un pulso mantenemos un latido suave en vez de un 1.0 plano
            self._opacity = 0.72 + 0.28 * (0.5 + 0.5 * math.sin(self._time * 7.0))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            sc = glow_scene(
                width=float(self.width()),
                height=float(self.height()),
                intensity=self._opacity * self._intensity,
                mode=self._mode,
                time_s=self._time,
                scan=self._scan,
            )
            paint_scene(painter, sc)
        finally:
            painter.end()


def preview_glow_scene(width: int = 1920, height: int = 1080, intensity: float = 0.85, mode: str = "capture") -> Scene:
    """Atajo para los tests de GUI y la previsualización sin Qt."""
    return glow_scene(float(width), float(height), intensity, mode, time_s=0.5, scan=True)
