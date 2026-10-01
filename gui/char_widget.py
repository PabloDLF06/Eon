"""El personaje vivo de EON dentro de un QWidget opaco-translúcido (spec 2.2).

El widget es deliberadamente tonto: no decide posturas ni tiempos, sólo avanza
el reloj de :class:`gui.char_kinematics.CharModel` y pinta lo que le devuelven.
Toda la coreografía (respiración, parpadeo, sacudidas, antenas, visor, zZ) es
Python puro y está cubierta por los tests.

Uso normal desde ``NotchWindow``::

    char = CharWidget()
    char.set_state("listening")      # opcional: el notch ya lo sincroniza
    # en el timer de 60 fps:
    char.advance(dt)
"""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QWidget

from gui import scene
from gui.char_kinematics import CharFrame, CharModel, CharState, CharTuningData, build_scene
from gui.qt_paint import paint_scene


def _char_tuning() -> CharTuningData:
    """Lee el ajuste del personaje desde ``config`` sin crear acoplamiento duro."""
    try:
        import config

        c = config.CHAR
        return CharTuningData(
            breath_period_s=c.breath_period_s,
            breath_amplitude=c.breath_amplitude,
            blink_interval_s=tuple(c.blink_interval_s),
            blink_close_s=c.blink_close_s,
            blink_hold_s=c.blink_hold_s,
            blink_open_s=c.blink_open_s,
            saccade_interval_s=tuple(c.saccade_interval_s),
            saccade_max_offset=c.saccade_max_offset,
            saccade_dwell_s=c.saccade_dwell_s,
            mouth_smile=c.mouth_smile,
            wake_pulse_s=c.wake_pulse_s,
            ear_perk_ms=c.ear_perk_ms,
            z_particle_count=c.z_particle_count,
            z_particle_period_s=c.z_particle_period_s,
            visor_scan_s=c.visor_scan_s,
            body_width_ratio=c.body_width_ratio,
            body_height_ratio=c.body_height_ratio,
            squircle_exponent=c.squircle_exponent,
            squircle_samples=c.squircle_samples,
        )
    except Exception:  # pragma: no cover - config siempre está disponible
        return CharTuningData()


def _palette() -> dict[str, str]:
    try:
        import config

        c = config.COLORS
        return {
            "top": c.char_top,
            "mid": c.char_mid,
            "bottom": c.char_bottom,
            "line": c.char_line,
            "blush": c.char_blush,
            "shine": c.char_shine,
        }
    except Exception:  # pragma: no cover
        return {}


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


class CharWidget(QWidget):
    """Cuadrado translúcido donde vive el personaje."""

    #: Se emite al cambiar de expresión; el notch lo usa para sincronizar el suyo.
    stateChanged = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        model: CharModel | None = None,
        autoplay: bool = False,
        fps: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._tuning = _char_tuning()
        self._palette = _palette()
        self._model = model or CharModel(tuning=self._tuning)
        self._autoplay = autoplay
        self._last_tick = QTimer(self)
        self._fps = max(12, int(fps or 60))
        self._last_tick.setInterval(int(1000 / self._fps))
        self._last_tick.timeout.connect(self._on_tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # sin tamaño fijo: el notch decide la caja; el personaje se escala solo
        self.setMinimumSize(24, 24)
        if self._autoplay:
            self._last_tick.start()

    # ------------------------------------------------------------------ API --
    @property
    def model(self) -> CharModel:
        return self._model

    def set_state(self, state: CharState | str) -> None:
        new_state = CharState.parse(state)
        if new_state is self._model.state:
            return
        self._model.set_state(new_state)
        self.stateChanged.emit(new_state.value)
        self.update()

    def state(self) -> CharState:
        return self._model.state

    def set_energy(self, level: float) -> None:
        """Envolvente de amplitud del TTS: sincroniza la boca (0..1)."""
        self._model.speak_level(scene.clamp01(level))

    def wake(self) -> None:
        self._model.trigger_wake()
        self.update()

    def advance(self, dt: float) -> CharFrame:
        """Adelanta el reloj del personaje (lo llama el timer del notch)."""
        return self._model.update(dt)

    def set_autoplay(self, enabled: bool) -> None:
        """Sin el notch (previsualización, tests de GUI) el widget se autoanimo."""
        self._autoplay = bool(enabled)
        if self._autoplay:
            self._last_tick.start()
        else:
            self._last_tick.stop()

    def frame(self) -> CharFrame:
        return self._model.frame

    def scene_at(self, box: tuple[float, float, float, float] | None = None) -> scene.Scene:
        """Escena actual; permite al notch pintar al personaje en su propio lienzo."""
        if box is None:
            box = (0.0, 0.0, float(self.width()), float(self.height()))
        return build_scene(self._model.frame, box, self._tuning, self._palette)

    # ------------------------------------------------------------- Qt hooks --
    def sizeHint(self):
        from PyQt6.QtCore import QSize

        side = min(48, max(24, self.width() if self.width() else 48))
        return QSize(side, side)

    def _on_tick(self) -> None:
        self._model.update(1.0 / self._fps)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            box = self._content_box()
            sc = build_scene(self._model.frame, box, self._tuning, self._palette)
            paint_scene(painter, sc)
        except Exception as exc:
            _log_paint_error("personaje", exc)
        finally:
            painter.end()

    def _content_box(self) -> tuple[float, float, float, float]:
        """Deja aire para las antenas y la onda radial, igual que la referencia."""
        w, h = float(self.width()), float(self.height())
        pad_x = min(6.0, w * 0.06)
        top_pad = min(h * 0.24, 9.0)
        bottom_pad = min(h * 0.05, 2.0)
        return (pad_x, top_pad, max(8.0, w - 2 * pad_x), max(8.0, h - top_pad - bottom_pad))
