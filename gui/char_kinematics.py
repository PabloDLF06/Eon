"""Cinemática del personaje vivo de EON (spec 2.2).

Todo lo que "siente" el personaje se calcula aquí, sin Qt: respiración,
parpadeo, sacudidas oculares, erección de las antenas, onda radial al despertar,
sincronía labial con la envolvente de TTS, visor HUD, partículas "zZ".

El módulo expone dos piezas:

* :class:`CharModel` -- reloj con estado; recibe ``update(dt, ...)`` a 60 fps y
  produce un :class:`CharFrame` con números puros.
* :func:`build_scene` -- traduce un fotograma a primitivas de ``gui.scene``.

La separación permite probar la coreografía sin ventana y que el widget
``gui/char_widget.py`` sea sólo un traductor ``QPainter`` tonto.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum

from gui import scene
from gui.scene import Paint, Scene, Shape, Stroke


class CharState(str, Enum):
    """Expresiones canónicas del personaje."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    VISION = "vision"
    HAPPY = "happy"
    ERROR = "error"
    SLEEPING = "sleeping"

    @classmethod
    def parse(cls, value: CharState | str) -> CharState:
        """Acepta ``CharState``, su nombre o el alias usado por el resto del sistema."""
        if isinstance(value, CharState):
            return value
        key = str(value or "").strip().lower()
        aliases = {
            "": "idle",
            "idle": "idle",
            "normal": "idle",
            "resting": "idle",
            "wake": "listening",
            "wakeup": "listening",
            "listening": "listening",
            "recording": "listening",
            "thinking": "thinking",
            "processing": "thinking",
            "swapping": "thinking",
            "speaking": "speaking",
            "tts": "speaking",
            "media": "speaking",
            "vision": "vision",
            "acting": "vision",
            "automation": "vision",
            "happy": "happy",
            "success": "happy",
            "proud": "happy",
            "error": "error",
            "alert": "error",
            "sleeping": "sleeping",
            "sleep": "sleeping",
            "muted": "sleeping",
            "dnd": "sleeping",
        }
        name = aliases.get(key, key)
        try:
            return cls(name)
        except ValueError:
            return cls.IDLE


@dataclass(frozen=True)
class CharTuningData:
    """Subconjunto de ajustes usado por la cinemática (llega desde ``config``)."""

    breath_period_s: float = 3.0
    breath_amplitude: float = 1.35
    blink_interval_s: tuple[float, float] = (4.0, 7.0)
    blink_close_s: float = 0.07
    blink_hold_s: float = 0.035
    blink_open_s: float = 0.11
    saccade_interval_s: tuple[float, float] = (1.1, 2.9)
    saccade_max_offset: float = 1.15
    saccade_dwell_s: float = 0.14
    mouth_smile: float = 0.28
    wake_pulse_s: float = 0.9
    ear_perk_ms: int = 180
    z_particle_count: int = 3
    z_particle_period_s: float = 2.4
    visor_scan_s: float = 1.6
    body_width_ratio: float = 0.90
    body_height_ratio: float = 0.82
    squircle_exponent: float = 3.2
    squircle_samples: int = 96


@dataclass(frozen=True)
class CharFrame:
    """Instantánea numérica del personaje; consume :func:`build_scene`."""

    state: CharState = CharState.IDLE
    time: float = 0.0
    #: desplazamiento vertical por la respiración, en píxeles
    bob: float = 0.0
    #: 1.0 = cuerpo natural; <1 aplastado (dormido), >1 estirado (alerta)
    squash: float = 1.0
    #: ligera inclinación de la cabeza en grados
    tilt: float = 0.0
    #: apertura ocular normalizada (0 = cerrado, 1 = normal, >1 = asombrado)
    eye_open: float = 1.0
    #: deriva del iris por sacudidas sacádicas, en píxeles
    eye_dx: float = 0.0
    eye_dy: float = 0.0
    #: apertura de boca 0..1, gobernada por la envolvente de TTS
    mouth_open: float = 0.0
    #: ancho relativo de la boca
    mouth_width: float = 1.0
    #: curvatura de sonrisa (-1 ceño .. 1 sonrisa amplia)
    smile: float = 0.0
    #: 0 = antenas caídas, 1 = erguidas al máximo
    ear_perk: float = 0.0
    #: progreso 0..1 de la onda radial de despertar; ``None`` = sin onda
    pulse: float | None = None
    halo_alpha: float = 0.0
    halo_color: str = "#00e5ff"
    blush_alpha: float = 0.55
    #: progreso 0..1 del barrido del visor HUD
    visor: float = 0.0
    #: partículas "zZ": (dx, dy, alpha, escala)
    zzz: tuple[tuple[float, float, float, float], ...] = ()
    #: chispa de éxito: 0..1
    sparkle: float = 0.0
    #: gota de sudor del estado de error: 0..1
    sweat: float = 0.0
    #: burbuja de elocución (los tres puntos del estado "pensando")
    emote: int = 0


@dataclass
class CharModel:
    """Reloj de estado del personaje.

    Instanciar uno por personaje. Llamar :meth:`update` una vez por fotograma.
    """

    tuning: CharTuningData = field(default_factory=CharTuningData)
    state: CharState = CharState.IDLE
    seed: int = 20261001

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._time = 0.0
        self._blink_timer = self._next_blink_delay()
        self._blink_phase = 0.0          # >0 mientras dura el parpadeo
        self._saccade_timer = self._next_saccade_delay()
        self._saccade_target = (0.0, 0.0)
        self._saccade_current = (0.0, 0.0)
        self._saccade_hold = 0.0
        self._state_started = 0.0
        self._wake_pulse = 0.0
        self._ear = 0.0
        self._mouth = 0.0
        self._mouth_target = 0.0
        self._energy = 0.0
        self._z_phase = 0.0
        self._frame = CharFrame(state=self.state)

    # ------------------------------------------------------------------ API --
    @property
    def time(self) -> float:
        return self._time

    def set_state(self, state: CharState | str) -> CharFrame:
        """Cambia de expresión y dispara las animaciones de entrada."""
        new_state = CharState.parse(state)
        if new_state is self.state:
            return self._frame
        self.state = new_state
        self._state_started = self._time
        if new_state in (CharState.LISTENING, CharState.SPEAKING, CharState.HAPPY):
            self._wake_pulse = 1e-6          # arranca la onda radial
            self._ear = max(self._ear, 0.15)
        if new_state is CharState.SLEEPING:
            self._z_phase = 0.0
        return self._frame

    def trigger_wake(self) -> None:
        """Palpalito de atención al oír la palabra de activación."""
        self._wake_pulse = 1e-6
        self._ear = 1e-6

    def speak_level(self, level: float) -> None:
        """Alimenta la envolvente de amplitud del TTS (0..1)."""
        self._energy = scene.clamp(level, 0.0, 1.0)

    @property
    def frame(self) -> CharFrame:
        return self._frame

    def update(self, dt: float, state: CharState | str | None = None, level: float | None = None) -> CharFrame:
        """Avanza el reloj ``dt`` segundos y devuelve el fotograma resultante."""
        dt = scene.clamp(float(dt), 0.0, 0.25)  # picar dt evita saltos al cambiar de ventana
        if state is not None:
            self.set_state(state)
        if level is not None:
            self.speak_level(level)
        self._time += dt
        self._advance_blink(dt)
        self._advance_saccade(dt)
        self._advance_mouth(dt)
        self._advance_pulse(dt)
        self._advance_ear(dt)
        self._advance_zzz(dt)
        self._frame = self._compose()
        return self._frame

    # -------------------------------------------------------------- internos --
    def _next_blink_delay(self) -> float:
        lo, hi = self.tuning.blink_interval_s
        if self.state in (CharState.LISTENING, CharState.VISION):
            lo, hi = lo * 0.7, hi * 0.7          # más atento => parpadeo más seco
        if self.state is CharState.SLEEPING:
            lo, hi = 9.0, 14.0                   # dormido: casi no parpadea
        return self._rng.uniform(lo, hi)

    def _advance_blink(self, dt: float) -> None:
        if self._blink_phase > 0.0:
            self._blink_phase += dt
            total = (
                self.tuning.blink_close_s
                + self.tuning.blink_hold_s
                + self.tuning.blink_open_s
            )
            if self._blink_phase >= total:
                self._blink_phase = 0.0
        else:
            self._blink_timer -= dt
            if self._blink_timer <= 0.0:
                self._blink_phase = 1e-6
                self._blink_timer = self._next_blink_delay()

    def _blink_openness(self) -> float:
        """0.0 = párpado cerrado, 1.0 = ojo abierto, 1.3 = ojos como platos."""
        t = self._blink_phase
        if t <= 0.0:
            return 1.0
        close, hold, open_ = (
            self.tuning.blink_close_s,
            self.tuning.blink_hold_s,
            self.tuning.blink_open_s,
        )
        if t <= close:
            return scene.lerp(1.0, 0.05, scene.smoothstep(0.0, close, t))
        t -= close
        if t <= hold:
            return 0.05
        t -= hold
        return scene.lerp(0.05, 1.0, scene.smoothstep(0.0, open_, t))

    def _next_saccade_delay(self) -> float:
        lo, hi = self.tuning.saccade_interval_s
        return self._rng.uniform(lo, hi)

    def _advance_saccade(self, dt: float) -> None:
        if self._saccade_hold > 0.0:
            self._saccade_hold -= dt
            k = 1.0 - math.exp(-dt * 26.0)      # persecución exponencial = "dart"
            self._saccade_current = (
                self._saccade_current[0] + (self._saccade_target[0] - self._saccade_current[0]) * k,
                self._saccade_current[1] + (self._saccade_target[1] - self._saccade_current[1]) * k,
            )
            return
        self._saccade_timer -= dt
        if self._saccade_timer <= 0.0:
            amp = self.tuning.saccade_max_offset
            if self.state in (CharState.SPEAKING, CharState.HAPPY):
                amp *= 1.6
            self._saccade_target = (
                self._rng.uniform(-amp, amp),
                self._rng.uniform(-amp * 0.55, amp * 0.35),
            )
            self._saccade_hold = self.tuning.saccade_dwell_s
            self._saccade_timer = self._next_saccade_delay()

    def _advance_mouth(self, dt: float) -> None:
        """Suaviza la envolvente: la boca debe abrirse rápido y cerrarse suave."""
        if self.state is CharState.SPEAKING:
            base = self._energy
            # añade modulación lingual para que no parezca una goma de silicio
            wobble = 0.14 * math.sin(self._time * 17.0) + 0.08 * math.sin(self._time * 29.3)
            self._mouth_target = scene.clamp(base * 0.82 + 0.18 + wobble * base, 0.0, 1.0)
            k = 1.0 - math.exp(-dt * 34.0)
        elif self.state is CharState.LISTENING:
            self._mouth_target = 0.06 + 0.30 * self._energy
            k = 1.0 - math.exp(-dt * 18.0)
        else:
            self._mouth_target = 0.0
            k = 1.0 - math.exp(-dt * 8.0)
        self._mouth += (self._mouth_target - self._mouth) * k
        self._energy *= math.exp(-dt * 6.0)   # decaimiento si nadie alimenta

    def _advance_pulse(self, dt: float) -> None:
        if self._wake_pulse > 0.0:
            self._wake_pulse = min(1.0, self._wake_pulse + dt / max(0.1, self.tuning.wake_pulse_s))
            if self._wake_pulse >= 1.0:
                self._wake_pulse = 0.0

    def _advance_ear(self, dt: float) -> None:
        want = 1.0 if self.state in (
            CharState.LISTENING,
            CharState.SPEAKING,
            CharState.THINKING,
            CharState.HAPPY,
            CharState.VISION,
        ) else 0.0
        rate = 1000.0 / max(1, self.tuning.ear_perk_ms)
        if want > self._ear:
            self._ear = min(1.0, self._ear + dt * rate)
        else:
            self._ear = max(0.0, self._ear - dt * rate * 0.55)

    def _advance_zzz(self, dt: float) -> None:
        if self.state is not CharState.SLEEPING:
            return
        self._z_phase = (self._z_phase + dt / max(0.4, self.tuning.z_particle_period_s)) % 1.0

    def _compose(self) -> CharFrame:
        t = self._time
        since = t - self._state_started
        tuning = self.tuning
        breath = math.sin(2.0 * math.pi * t / tuning.breath_period_s)
        state = self.state

        bob = breath * tuning.breath_amplitude
        squash = 1.0
        tilt = 0.0
        eye_open = self._blink_openness()
        halo_alpha = 0.0
        halo_color = "#00e5ff"
        blush = 0.5
        smile = tuning.mouth_smile
        visor = 0.0
        sparkle = 0.0
        sweat = 0.0
        emote = 0

        if state is CharState.IDLE:
            halo_alpha = 0.10 + 0.06 * (breath * 0.5 + 0.5)
            halo_color = "#cbb7ff"
            tilt = 1.4 * math.sin(2.0 * math.pi * t / 7.0)
        elif state is CharState.LISTENING:
            eye_open = max(eye_open, 1.22 - 0.18 * scene.smoothstep(0.0, 0.25, since))
            halo_alpha = 0.42 + 0.18 * math.sin(2.0 * math.pi * t / 0.7)
            halo_color = "#00e5ff"
            blush = 0.68
            squash = 1.045 + 0.012 * breath
            bob *= 1.5
            smile = 0.34
        elif state is CharState.THINKING:
            eye_open = 0.82
            # la mirada se va arriba y a un lado mientras "procesa"
            self._saccade_target = (
                scene.clamp(self._saccade_target[0] + 0.35 * math.sin(t * 1.3), -2.0, 2.4),
                -1.15 - 0.45 * math.sin(t * 0.9),
            )
            halo_alpha = 0.30 + 0.10 * math.sin(2.0 * math.pi * t / 1.9)
            halo_color = "#a78bfa"
            emote = 1 + int(t * 2.2) % 3          # puntos rotativos
            smile = 0.05
            tilt = 2.2 * math.sin(2.0 * math.pi * t / 4.4)
        elif state is CharState.SPEAKING:
            halo_alpha = 0.30 + 0.16 * self._mouth
            halo_color = "#00e5ff"
            eye_open = max(eye_open, 0.92)
            smile = 0.42
            bob += 0.45 * math.sin(2.0 * math.pi * t / 1.15)
            blush = 0.62
        elif state is CharState.VISION:
            visor = 1.0  # el visor se mantiene puesto; lo que se mueve es el barrido
            eye_open = 1.0
            halo_alpha = 0.34
            halo_color = "#00f0ff"
            smile = 0.0
            squash = 1.02
        elif state is CharState.HAPPY:
            eye_open = 0.0                          # ojos felices en arco
            halo_alpha = 0.36
            halo_color = "#4ade80"
            smile = 0.95
            sparkle = scene.smoothstep(0.0, 0.35, since) * (1.0 - scene.smoothstep(0.9, 1.6, since))
            bob += 1.8 * abs(math.sin(2.0 * math.pi * t / 0.55))
            blush = 0.85
        elif state is CharState.ERROR:
            eye_open = 1.25
            halo_alpha = 0.30
            halo_color = "#ff6b8a"
            smile = -0.55
            sweat = scene.smoothstep(0.0, 0.4, since)
            tilt = 3.0 * math.sin(2.0 * math.pi * t / 0.42)
            squash = 0.985
        elif state is CharState.SLEEPING:
            eye_open = 0.0
            halo_alpha = 0.10
            halo_color = "#7f8bd6"
            smile = 0.16
            squash = 0.90 + 0.02 * breath
            bob = breath * tuning.breath_amplitude * 0.6
            tilt = -3.5

        zzz: tuple[tuple[float, float, float, float], ...] = ()
        if state is CharState.SLEEPING:
            count = max(1, tuning.z_particle_count)
            items: list[tuple[float, float, float, float]] = []
            for i in range(count):
                prog = (self._z_phase + i / count) % 1.0
                alpha = math.sin(math.pi * prog) * 0.9
                items.append(
                    (
                        0.30 + 0.26 * prog + 0.07 * i,
                        -0.10 - 0.62 * prog,
                        alpha,
                        0.55 + 0.55 * prog + 0.12 * i,
                    )
                )
            zzz = tuple(items)

        pulse = self._wake_pulse if self._wake_pulse > 0.0 else None
        return CharFrame(
            state=state,
            time=t,
            bob=bob,
            squash=squash,
            tilt=tilt,
            eye_open=eye_open,
            eye_dx=self._saccade_current[0],
            eye_dy=self._saccade_current[1],
            mouth_open=self._mouth,
            mouth_width=1.0 - 0.30 * self._mouth,
            smile=smile,
            ear_perk=self._ear,
            pulse=pulse,
            halo_alpha=halo_alpha,
            halo_color=halo_color,
            blush_alpha=blush,
            visor=visor,
            zzz=zzz,
            sparkle=sparkle,
            sweat=sweat,
            emote=emote,
        )


# --------------------------------------------------------------------------- #
# Escena vectorial
# --------------------------------------------------------------------------- #

def build_scene(
    frame: CharFrame,
    box: tuple[float, float, float, float],
    tuning: CharTuningData | None = None,
    colors: dict[str, str] | None = None,
) -> Scene:
    """Construye las primitivas del personaje dentro de ``box=(x, y, w, h)``.

    ``colors`` admite las claves ``top``, ``mid``, ``bottom``, ``line``,
    ``blush``, ``shine``; los valores por defecto reproducen la piel marfil con
    sombra lavanda de la referencia.
    """
    tuning = tuning or CharTuningData()
    palette = {
        "top": "#fdf6f1",
        "mid": "#f3e7e6",
        "bottom": "#ddd0f0",
        "line": "#1a1721",
        "blush": "#f7a8bb",
        "shine": "#ffffff",
    }
    palette.update(colors or {})

    x, y, w, h = box
    cx = x + w / 2.0
    cy = y + h / 2.0 + frame.bob
    half_w = max(3.0, (w / 2.0) * tuning.body_width_ratio)
    half_h = max(3.0, (h / 2.0) * tuning.body_height_ratio)

    shapes: list[Shape] = []

    # -- halo ambiental (detrás del cuerpo) -------------------------------- #
    if frame.halo_alpha > 0.02:
        shapes.append(
            Shape(
                points=tuple(scene.ellipse_points(cx, cy, half_w * 1.62, half_h * 1.78, samples=48)),
                fill=Paint(color=frame.halo_color, alpha=frame.halo_alpha * 0.32, radial=True,
                           focal=(cx, cy - half_h * 0.2)),
                closed=True,
                tag="halo",
                z=-30,
            )
        )

    # -- onda radial de despertar ------------------------------------------ #
    if frame.pulse is not None:
        for i in range(3):
            prog = scene.clamp01(frame.pulse * 1.35 - i * 0.18)
            if prog <= 0.0 or prog >= 1.0:
                continue
            radius = half_w * (0.75 + 1.75 * prog)
            shapes.append(
                Shape(
                    points=tuple(scene.ellipse_points(cx, cy, radius, radius * (half_h / half_w), samples=56)),
                    stroke=Stroke(color="#00e5ff", width=2.4 * (1.0 - prog) + 0.5,
                                  alpha=(1.0 - prog) ** 1.6 * 0.85),
                    closed=True,
                    tag="wake-ring",
                    z=-20,
                )
            )

    # -- cuerpo ------------------------------------------------------------ #
    body_points = scene.scale_points(
        scene.squircle_points(
            cx,
            cy,
            half_w,
            half_h,
            exponent=tuning.squircle_exponent,
            samples=tuning.squircle_samples,
            bottom_bias=1.05,
        ),
        (cx, cy + half_h * 0.55),
        1.0,
        1.0 / max(0.2, frame.squash),
        angle_deg=frame.tilt,
    )
    shapes.append(
        Shape(
            points=tuple(body_points),
            fill=Paint(color=palette["top"], alpha=1.0, to_color=palette["bottom"]),
            closed=True,
            tag="body",
            z=0,
        )
    )
    # volumen: especular superior + sombra inferior, como en la referencia
    shapes.append(
        Shape(
            points=tuple(
                scene.ellipse_points(cx - half_w * 0.28, cy - half_h * 0.46, half_w * 0.42, half_h * 0.27, samples=36)
            ),
            fill=Paint(color=palette["shine"], alpha=0.20, radial=True),
            closed=True,
            tag="shine",
            z=1,
        )
    )
    shapes.append(
        Shape(
            points=tuple(body_points),
            stroke=Stroke(color=palette["bottom"], width=1.1, alpha=0.55),
            closed=True,
            tag="rim",
            z=2,
        )
    )

    # -- antenas / orejas --------------------------------------------------- #
    # La base se esconde bajo el cuerpo (z=-1) y sólo asoma la curva: es lo que
    # hace que el personaje parezca "pelo/oreja" en vez de dos palos pegados.
    perk = scene.clamp01(frame.ear_perk)
    for side in (-1.0, 1.0):
        sway = 0.055 * math.sin(frame.time * 2.1 + (0.0 if side < 0 else math.pi))
        base = (cx + side * half_w * 0.44, cy - half_h * 0.70)
        rise = half_h * scene.lerp(0.40, 0.66, perk)
        spread = half_w * scene.lerp(0.40, 0.26, perk) * side
        tip = (base[0] + spread + half_w * sway, base[1] - rise)
        ctrl = (base[0] + spread * scene.lerp(0.85, 0.30, perk), base[1] - rise * 0.42)
        shapes.append(
            Shape(
                points=tuple(scene.bezier_points(base, ctrl, tip, samples=18)),
                stroke=Stroke(color=palette["top"], width=max(1.6, half_w * 0.13), alpha=1.0),
                closed=False,
                tag="ear",
                z=-1,
            )
        )
        shapes.append(
            Shape(
                points=tuple(scene.ellipse_points(tip[0], tip[1], half_w * 0.075, half_w * 0.075, samples=18)),
                fill=Paint(color="#dff7ff" if perk > 0.05 else palette["mid"], alpha=0.55 + 0.45 * perk),
                stroke=Stroke(color="#00e5ff", width=1.0, alpha=0.55 * perk, glow=3.2 * perk)
                if perk > 0.02
                else None,
                closed=True,
                tag="ear-tip",
                z=3,
            )
        )

    # -- mejillas ----------------------------------------------------------- #
    if frame.blush_alpha > 0.03:
        for side in (-1.0, 1.0):
            bx = cx + side * half_w * 0.62
            by = cy + half_h * 0.20
            shapes.append(
                Shape(
                    points=tuple(scene.ellipse_points(bx, by, half_w * 0.20, half_h * 0.11, samples=26)),
                    fill=Paint(color=palette["blush"], alpha=frame.blush_alpha * 0.95, radial=True),
                    closed=True,
                    tag="blush",
                    z=4,
                )
            )

    # -- ojos --------------------------------------------------------------- #
    eye_y = cy + half_h * 0.06
    eye_dx = half_w * 0.30
    eye_r = half_w * 0.115
    openness = scene.clamp(frame.eye_open, 0.0, 1.45)
    happy_arcs = frame.state in (CharState.HAPPY, CharState.SLEEPING) and frame.smile > 0.1
    for side in (-1.0, 1.0):
        ex = cx + side * eye_dx + frame.eye_dx
        ey = eye_y + frame.eye_dy
        if happy_arcs:
            # "^ ^": dos arcos hacia arriba en vez de discos
            pts = scene.ellipse_points(ex, ey + eye_r * 0.35, eye_r * 1.5, eye_r * 1.25,
                                        samples=18, start_deg=200.0, span_deg=140.0)
            shapes.append(
                Shape(
                    points=tuple(pts),
                    stroke=Stroke(color=palette["line"], width=max(1.5, eye_r * 0.42), alpha=1.0),
                    closed=False,
                    tag="eye",
                    z=6,
                )
            )
        elif openness < 0.16:
            shapes.append(
                Shape(
                    points=((ex - eye_r, ey), (ex + eye_r, ey)),
                    stroke=Stroke(color=palette["line"], width=max(1.4, eye_r * 0.38), alpha=0.95),
                    closed=False,
                    tag="eye",
                    z=6,
                )
            )
        else:
            ry = eye_r * openness
            shapes.append(
                Shape(
                    points=tuple(scene.ellipse_points(ex, ey, eye_r, ry, samples=28)),
                    fill=Paint(color=palette["line"], alpha=1.0),
                    closed=True,
                    tag="eye",
                    z=6,
                )
            )
            if openness > 0.6:
                shapes.append(
                    Shape(
                        points=tuple(
                            scene.ellipse_points(ex - eye_r * 0.30, ey - ry * 0.38, eye_r * 0.30, ry * 0.26, samples=14)
                        ),
                        fill=Paint(color="#ffffff", alpha=0.85),
                        closed=True,
                        tag="eye-spark",
                        z=7,
                    )
                )

    # -- visor HUD ---------------------------------------------------------- #
    if frame.visor > 0.02:
        vx, vy = cx - half_w * 0.80, cy - half_h * 0.24
        vw, vh = half_w * 1.60, half_h * 0.44
        pts = scene.rounded_rect_points(vx, vy, vw, vh, vh * 0.45, vh * 0.45, samples=8)
        shapes.append(
            Shape(
                points=tuple(pts),
                fill=Paint(color="#04222b", alpha=0.82, to_color="#0a4c5e"),
                stroke=Stroke(color="#00f0ff", width=1.5, alpha=0.95, glow=4.2),
                closed=True,
                tag="visor",
                z=9,
            )
        )
        scan_x = vx + vw * scene.clamp01((math.sin(2.0 * math.pi * frame.time / 1.6) + 1.0) / 2.0)
        shapes.append(
            Shape(
                points=((scan_x, vy + vh * 0.12), (scan_x, vy + vh * 0.88)),
                stroke=Stroke(color="#a8fbff", width=1.6, alpha=0.85, glow=2.4),
                closed=False,
                tag="visor-scan",
                z=10,
            )
        )
        for sx, sy in ((vx, vy), (vx + vw, vy), (vx, vy + vh), (vx + vw, vy + vh)):
            shapes.append(
                Shape(
                    points=((sx, sy), (sx + (6 if sx == vx else -6), sy)),
                    stroke=Stroke(color="#00f0ff", width=1.2, alpha=0.65),
                    closed=False,
                    tag="visor-tick",
                    z=10,
                )
            )

    # -- boca --------------------------------------------------------------- #
    mouth_y = cy + half_h * 0.44
    open_ = frame.mouth_open
    width = half_w * 0.20 * frame.mouth_width
    if open_ > 0.08:
        mh = half_h * (0.06 + 0.34 * open_)
        shapes.append(
            Shape(
                points=tuple(scene.ellipse_points(cx, mouth_y, width * (1.0 + 0.45 * open_), mh, samples=26)),
                fill=Paint(color="#3a1f2c", alpha=0.94),
                stroke=Stroke(color=palette["line"], width=1.1, alpha=0.6),
                closed=True,
                tag="mouth",
                z=8,
            )
        )
        shapes.append(
            Shape(
                points=tuple(scene.ellipse_points(cx, mouth_y + mh * 0.35, width * 0.55, mh * 0.35, samples=18)),
                fill=Paint(color="#e57f9b", alpha=0.75),
                closed=True,
                tag="tongue",
                z=9,
            )
        )
    else:
        curve = frame.smile * half_h * 0.16
        shapes.append(
            Shape(
                points=tuple(
                    scene.bezier_points(
                        (cx - width, mouth_y - max(0.0, curve) * 0.2),
                        (cx, mouth_y + curve),
                        (cx + width, mouth_y - max(0.0, curve) * 0.2),
                        samples=16,
                    )
                ),
                stroke=Stroke(color=palette["line"], width=max(1.4, half_w * 0.055), alpha=0.92),
                closed=False,
                tag="mouth",
                z=8,
            )
        )

    # -- chispas de éxito y sudor de error ---------------------------------- #
    if frame.sparkle > 0.02:
        for i, (fx, fy) in enumerate(((-0.95, -0.72), (0.92, -0.42), (-0.62, 0.66))):
            sx, sy = cx + half_w * fx, cy + half_h * fy
            r = half_w * (0.16 + 0.10 * math.sin(frame.time * 9.0 + i)) * frame.sparkle
            shapes.append(
                Shape(
                    points=((sx - r, sy), (sx + r, sy), (sx, sy - r), (sx, sy + r)),
                    stroke=Stroke(color="#4ade80", width=1.6, alpha=0.85 * frame.sparkle, glow=3.0),
                    closed=False,
                    tag="sparkle",
                    z=12,
                )
            )
    if frame.sweat > 0.02:
        drop = (cx + half_w * 0.86, cy - half_h * (0.70 - 0.45 * frame.sweat))
        shapes.append(
            Shape(
                points=tuple(
                    scene.bezier_points(
                        (drop[0], drop[1] - half_h * 0.20),
                        (drop[0] + half_w * 0.13, drop[1] + half_h * 0.02),
                        (drop[0], drop[1] + half_h * 0.14),
                        samples=12,
                    )
                )
                + tuple(
                    scene.bezier_points(
                        (drop[0], drop[1] + half_h * 0.14),
                        (drop[0] - half_w * 0.13, drop[1] + half_h * 0.02),
                        (drop[0], drop[1] - half_h * 0.20),
                        samples=12,
                    )
                ),
                fill=Paint(color="#9fe8ff", alpha=0.9),
                stroke=Stroke(color="#00f0ff", width=1.0, alpha=0.7),
                closed=True,
                tag="sweat",
                z=12,
            )
        )

    # -- partículas zZ (vectoriales, sin depender de fuentes) --------------- #
    for dx_ratio, dy_ratio, alpha, scale in frame.zzz:
        zx = cx + half_w * (0.95 + dx_ratio)
        zy = cy + half_h * (-0.85 + dy_ratio)
        size = half_w * 0.30 * scale
        pts = (
            (zx - size * 0.5, zy - size * 0.5),
            (zx + size * 0.5, zy - size * 0.5),
            (zx - size * 0.5, zy + size * 0.5),
            (zx + size * 0.5, zy + size * 0.5),
        )
        shapes.append(
            Shape(
                points=pts,
                stroke=Stroke(color="#b9c4ff", width=max(1.2, size * 0.18), alpha=alpha * 0.9),
                closed=False,
                tag="zzz",
                z=13,
            )
        )

    # -- burbuja de elocución (tres puntos) --------------------------------- #
    if frame.emote:
        bw, bh = half_w * 1.28, half_h * 0.52
        bx, by = cx - half_w * 1.02, cy - half_h * 1.42
        shapes.append(
            Shape(
                points=tuple(scene.rounded_rect_points(bx, by, bw, bh, bh * 0.5, bh * 0.5, samples=8)),
                fill=Paint(color="#151323", alpha=0.96),
                stroke=Stroke(color="#a78bfa", width=1.4, alpha=0.85, glow=4.0),
                closed=True,
                tag="emote",
                z=14,
            )
        )
        for i in range(3):
            on = 1.0 if (i == frame.emote % 3) else 0.35
            shapes.append(
                Shape(
                    points=tuple(
                        scene.ellipse_points(
                            bx + bw * (0.28 + 0.22 * i), by + bh / 2.0, bh * 0.13, bh * 0.13, samples=16
                        )
                    ),
                    fill=Paint(color="#d9ccff", alpha=on),
                    closed=True,
                    tag="emote-dot",
                    z=15,
                )
            )

    return Scene(width=w, height=h, shapes=tuple(shapes), meta={"state": frame.state.value})


def frames_preview(
    seconds: float = 2.0,
    fps: int = 12,
    states: Sequence[str] | None = None,
    tuning: CharTuningData | None = None,
) -> dict[str, list[CharFrame]]:
    """Genera secuencias de fotogramas por estado (usado por la previsualización)."""
    tuning = tuning or CharTuningData()
    out: dict[str, list[CharFrame]] = {}
    for name in (states or [s.value for s in CharState]):
        model = CharModel(tuning=tuning)
        model.set_state(name)
        frames: list[CharFrame] = []
        step = 1.0 / max(1, fps)
        t = 0.0
        while t < seconds:
            level = 0.65 * (0.5 + 0.5 * math.sin(t * 11.0)) if name == "speaking" else 0.0
            frames.append(replace(model.update(step, level=level)))
            t += step
        out[name] = frames
    return out
