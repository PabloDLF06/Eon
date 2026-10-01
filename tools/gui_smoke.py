#!/usr/bin/env python3
"""Prueba de humo de la interfaz real: levanta Qt y *pinta* cada estado.

Existe porque la suite normal vive sin GUI y ahí un enum de Qt mal escrito es
invisible… pero en Windows esa tontería aborta el proceso en el primer
``paintEvent`` y el usuario sólo ve un parpadeo. Este script fuerza un
``grab()`` (pintado real con QPainter) por cada estado del notch, enciende y
apaga el glow, y recorre las señales del puente; si algo revienta, sale con
código distinto y la traza en stderr.

Se ejecuta siempre como *subproceso* (pytest lo lanza igual): un fallo de Qt
no debe poder tumbar el proceso de pruebas. Fuera de Windows se usa la
plataforma ``offscreen``::

    python tools/gui_smoke.py            # devuelve 0 si la GUI respira
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATES = ("idle", "listening", "thinking", "media", "action", "error", "sleeping")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:
        print(f"sin PyQt6: {exc}", file=sys.stderr)
        return 90  # no es un fallo de EON: falta la dependencia

    app = QApplication.instance() or QApplication([sys.argv[0]])

    from gui.char_widget import CharWidget
    from gui.notch_layout import MediaInfo, NotchState
    from gui.notch_window import NotchWindow
    from gui.screen_glow import ScreenGlow

    failures: list[str] = []
    notch = NotchWindow()
    notch.show()
    glow = ScreenGlow()

    for name in STATES:
        try:
            notch.request(name, status=f"estado {name}", detail="una linea de prueba legible")
            app.processEvents()
            image = notch.grab()
            if image.isNull():
                failures.append(f"{name}: grab devolvió imagen nula")
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    try:  # la tarjeta de música con metadatos reales y su ecualizador
        notch.controller.set_media(MediaInfo(title="Loser", artist="Tame Impala", source="Spotify", playing=True))
        notch.request(NotchState.MEDIA)
        for _ in range(30):
            app.processEvents()
        if notch.grab().isNull():
            failures.append("media: grab nulo tras animar")
    except Exception as exc:
        failures.append(f"media: {type(exc).__name__}: {exc}")

    try:  # glow: encender, pulsar y apagar (animación de opacidad incluida)
        glow.begin("act")
        glow.pulse("vision", duration_ms=120)
        app.processEvents()
        if glow.grab().isNull():
            failures.append("glow: grab nulo")
        glow.end()
    except Exception as exc:
        failures.append(f"glow: {type(exc).__name__}: {exc}")

    try:  # el personaje, también como widget independiente (embed_character=False)
        solo = NotchWindow(embed_character=False)
        solo.show()
        solo.request(NotchState.LISTENING)
        app.processEvents()
        solo.grab()
        if not isinstance(solo.char_widget, CharWidget):
            failures.append("char: el widget hijo no es CharWidget")
        solo.close()
    except Exception as exc:
        failures.append(f"char: {type(exc).__name__}: {exc}")

    try:  # ciclo completo de kill switch sobre la ventana viva
        notch.controller.collapse()
        notch.rest()
        app.processEvents()
        notch.grab()
    except Exception as exc:
        failures.append(f"kill: {type(exc).__name__}: {exc}")

    notch._ticker.stop()
    glow._ticker.stop()

    # El blindaje de paintEvent convierte los fallos de pintado en log (con
    # ráfagas silenciadas a 30 s), no en abort: el smoke lee los contadores de
    # cada módulo — si alguno registró un fallo, la pintura está rota de verdad.
    from gui import char_widget, notch_window, screen_glow

    painted_errors = []
    for label, module in (("notch", notch_window), ("glow", screen_glow), ("personaje", char_widget)):
        for where, when in getattr(module, "_paint_errors", {}).items():
            if when > 0:
                painted_errors.append(f"{label}:{where}")
    if painted_errors:
        failures.append("errores de pintado registrados: " + ", ".join(sorted(painted_errors)))

    if failures:
        print("FALLOS DE GUI:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("gui-smoke: todos los estados pintan sin excepciones")
    return 0


if __name__ == "__main__":
    sys.exit(main())
