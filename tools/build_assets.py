#!/usr/bin/env python3
"""Genera los assets reales del proyecto (retratos PNG del personaje y sonidos UI).

El personaje se dibuja por vectores en tiempo real — estos PNG son la misma
geometría congelada, y sirven para:

* el icono de la aplicación (bandeja, barra de tareas, ``QApplication``);
* la documentación del README;
* depurar a ojo un cambio en ``gui/char_kinematics.py`` sin levantar Qt.

Los WAV son cortos (menos de un segundo), sintetizados aquí mismo, sin
terceros ni descargas: ``boot`` (bienvenida), ``ack`` (palmada reconocida),
``alarm`` (kill switch). Regenerar todo tras tocar el motor visual::

    python tools/build_assets.py
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.scene_render import render_scene_to_image

CHAR_DIR = ROOT / "assets" / "char"
SOUND_DIR = ROOT / "assets" / "sounds"

#: estados del personaje que merecen retrato propio
POSES = ("idle", "listening", "thinking", "speaking", "wake", "sleeping")
CANVAS = (240, 210)


def build_pngs(scale: float = 2.0) -> list[Path]:
    """Un PNG transparente por estado, con el mismo modelo de cinemática que la GUI."""
    from gui.char_kinematics import CharModel, build_scene
    from gui.scene import Scene

    out: list[Path] = []
    for state in POSES:
        model = CharModel()
        model.set_state(state)
        frame = None
        for index in range(70):  # 70 fotogramas: parpadeo y respiración estabilizados
            level = 0.6 if state in {"speaking", "listening"} and index % 8 < 4 else 0.0
            frame = model.update(1 / 60.0, level=level)
        assert frame is not None
        built = build_scene(frame, (14, 18, CANVAS[0] - 28, CANVAS[1] - 30))
        scene = Scene(width=float(CANVAS[0]), height=float(CANVAS[1]), shapes=built.shapes, clip=built.clip, meta=built.meta)
        image = render_scene_to_image(scene, scale=scale, supersample=2, background=(0, 0, 0, 0))
        path = CHAR_DIR / f"eon-{state}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        out.append(path)
    return out


# --------------------------------------------------------------------------- #
# Síntesis de sonido (sin dependencias más allá de la stdlib)
# --------------------------------------------------------------------------- #


def _envelope(length: int, attack: float = 0.01, release: float = 0.25) -> list[float]:
    att = max(1, int(length * attack))
    rel = max(1, int(length * release))
    env = []
    for i in range(length):
        if i < att:
            env.append(i / att)
        elif i > length - rel:
            t = (length - i) / rel
            env.append(t * t)
        else:
            env.append(1.0)
    return env


def _tone(samples: list[float], sample_rate: int, freq: float, start: float, length: float, gain: float = 0.5, attack: float = 0.01) -> None:
    offset = int(start * sample_rate)
    count = int(length * sample_rate)
    env = _envelope(count, attack=attack, release=min(0.6, max(0.05, length * 0.4)))
    for i in range(count):
        index = offset + i
        if 0 <= index < len(samples):
            value = math.sin(2 * math.pi * freq * i / sample_rate)
            # pequeño batido para que no suene a generador de laboratorio
            value += 0.35 * math.sin(2 * math.pi * freq * 2.005 * i / sample_rate)
            samples[index] += gain * value * env[i]


def _clip(samples: list[float]) -> bytes:
    peak = max((abs(v) for v in samples), default=1.0)
    scale = 0.86 * 32767 / max(peak, 1e-6)
    return struct.pack(f"<{len(samples)}h", *(max(-32767, min(32767, int(v * scale))) for v in samples))


def write_wav(path: Path, frames: list[float], sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


def build_wavs() -> list[Path]:
    """Tres avisos breves; el diseño es deliberadamente discreto (no sobresalta)."""
    rate = 44100
    out: list[Path] = []

    boot = [0.0] * int(rate * 1.15)
    _tone(boot, rate, 523.25, 0.00, 0.22, 0.45)   # do5
    _tone(boot, rate, 659.25, 0.16, 0.24, 0.45)   # mi5
    _tone(boot, rate, 783.99, 0.34, 0.42, 0.50)   # sol5, deja respirar la cola
    path = SOUND_DIR / "boot.wav"
    write_wav(path, _clip(boot), rate)
    out.append(path)

    ack = [0.0] * int(rate * 0.18)
    _tone(ack, rate, 880.0, 0.00, 0.09, 0.55, attack=0.002)
    _tone(ack, rate, 1174.7, 0.05, 0.10, 0.35, attack=0.002)  # re6, brillo corto
    path = SOUND_DIR / "ack.wav"
    write_wav(path, _clip(ack), rate)
    out.append(path)

    alarm = [0.0] * int(rate * 0.62)
    for beat, start in ((0, 0.0), (1, 0.21), (2, 0.42)):
        _tone(alarm, rate, 392.0 if beat % 2 == 0 else 311.1, start, 0.16, 0.60, attack=0.001)
    path = SOUND_DIR / "alarm.wav"
    write_wav(path, _clip(alarm), rate)
    out.append(path)

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera assets/char/*.png y assets/sounds/*.wav.")
    parser.add_argument("--only", choices=("png", "wav"), help="saltar una de las dos mitades")
    parser.add_argument("--scale", type=float, default=2.0, help="escala de los PNG (2 = icono nítido)")
    args = parser.parse_args(argv)

    made: list[Path] = []
    if args.only in (None, "png"):
        made += build_pngs(scale=args.scale)
    if args.only in (None, "wav"):
        made += build_wavs()
    for path in made:
        try:
            label = str(path.relative_to(ROOT))
        except ValueError:
            label = str(path)
        print(f"✓ {label} ({path.stat().st_size:,} bytes)")
    if not made:
        print("nada que generar", file=sys.stderr)
        return 1
    print(f"{len(made)} archivos en {ROOT / 'assets'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
