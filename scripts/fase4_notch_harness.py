"""Let Pablo exercise the real notch, settings, launchers, text and opt-in RMS."""

from pathlib import Path
from collections.abc import Callable
import logging
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QCheckBox, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.eon_state import EonState, STATE_LABELS
from gui.notch_window import NotchController, NotchWindow

logger = logging.getLogger(__name__)


class ControlWindow(QWidget):
    """Expose seven logical states, three flags and explicit geometry controls."""

    def __init__(self, notch: NotchWindow) -> None:
        super().__init__()
        self.notch = notch
        self.setWindowTitle("Eon — Prueba manual de Fase 4")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        instructions = QLabel("El notch está arriba, en el centro de la pantalla principal.\n"
                              "La cápsula comunica el estado por una onda radial suave.\n"
                              "Personaje oculto en reposo y hover; clic para panel completo.\n"
                              "Reposo sin actividad: se oculta tras el tiempo configurado.\n"
                              "Visión marcada fuerza expansión e impide colapsar.\n"
                              "Los siete botones solo simulan estados; no activan IA.\n"
                              "En el panel: engranaje para guardar ajustes y accesos reales.\n"
                              "Texto + Enter/Enviar: acuse honesto, sin respuesta de IA.\n"
                              "Micrófono: captura RMS REAL de 3 s, sin STT ni grabación.\n"
                              "Los accesos configurados sí abren aplicaciones/rutas reales.")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        states = QGridLayout()
        for index, state in enumerate(EonState):
            button = QPushButton(STATE_LABELS[state])
            button.setMinimumHeight(44)
            button.clicked.connect(lambda checked=False, selected=state: self.change_state(selected))
            states.addWidget(button, index // 2, index % 2)
        layout.addLayout(states)
        for text, method in (
            ("Actividad de voz (bloquea auto-hide)", notch.controller.notify_voice_activity),
            ("Borrador activo (bloquea auto-hide)", notch.controller.notify_draft_activity),
            ("Visión activa (fuerza expansión)", notch.controller.notify_vision_active),
        ):
            checkbox = QCheckBox(text)
            checkbox.toggled.connect(lambda active, notify=method: self.change_flag(notify, active))
            layout.addWidget(checkbox)
        for text, method in (("Expandir notch", notch.controller.expand), ("Colapsar notch", notch.controller.collapse)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, action=method: self.change_geometry(action))
            layout.addWidget(button)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        quit_button = QPushButton("Cerrar la prueba")
        quit_button.clicked.connect(self.close)
        layout.addWidget(quit_button)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.update_status)
        self._timer.start()
        self.update_status()

    def closeEvent(self, event: object) -> None:
        """Stop controls and let the notch release an active microphone before exiting."""
        self._timer.stop()
        self.notch.close()
        super().closeEvent(event)

    def change_state(self, state: EonState) -> None:
        """Apply one logical state while leaving the independent flags unchanged."""
        self.notch.controller.set_eon_state(state)
        self.notch.refresh()
        print(f"Estado lógico: {STATE_LABELS[state]}", flush=True)

    def change_flag(self, notify: Callable[[bool], None], active: bool) -> None:
        """Apply a control checkbox to its corresponding activity notification."""
        notify(active)
        self.notch.refresh()

    def change_geometry(self, action: Callable[[], None]) -> None:
        """Request a manual expansion/collapse with vision priority preserved."""
        action()
        self.notch.refresh()

    def update_status(self) -> None:
        """Show geometry separately from logical activity and peripheral errors."""
        controller = self.notch.controller
        self._status.setText(f"Estado: {STATE_LABELS[controller.eon_state]} · Geometría: {controller.geometry_state.name}\n"
                             f"Voz: {controller.voice_active} · Borrador: {controller.draft_active} · Visión: {controller.vision_active}\n"
                             f"Pantalla: {self.notch.last_error or 'sin errores registrados'}")


def main() -> int:
    """Launch a real desktop harness, handling startup failures with logging."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Eon — Diagnóstico notch")
        app.setQuitOnLastWindowClosed(False)
        notch = NotchWindow(NotchController())
        notch.controller.expand()
        notch.refresh()
        controls = ControlWindow(notch)
        notch.closed.connect(app.quit)
        print("Prueba manual de Eon: los siete botones cambian el estado, rostro, tinte e insignia del personaje.\n"
              "La cápsula oscura presenta un degradado suave y onda radial; el personaje solo aparece expandido.\n"
              "Las tres casillas notifican voz, borrador y visión por separado.\n"
              "Expandir/Colapsar cambian la geometría; visión activa tiene prioridad.\n"
              "Pasa el ratón por el notch para ver HOVER_PEEK; haz clic para EXPANDED.\n"
              "Esc colapsa solo cuando el notch expandido tiene foco; no es una hotkey global.\n"
              "Para probar auto-hide: Reposo, casillas desmarcadas, Expandir y ratón fuera.\n"
              "Engranaje: edita auto-hide y accesos, guarda y comprueba aplicación sin reiniciar.\n"
              "Accesos: abre una ruta real configurada; una ruta ausente debe mostrar aviso.\n"
              "Escribe y usa Enter/Enviar: campo limpio y acuse, sin respuesta simulada.\n"
              "Micrófono: captura real breve y medidor en vivo, sin STT/brain ni guardar audio.\n"
              "Cerrar la prueba o cerrar la ventana de control termina ambas ventanas.\n"
              "Verde de Visión y tamaños pendientes de tu validación visual; no se carga ningún modelo.", flush=True)
        notch.show()
        controls.show()
        return app.exec()
    except Exception:
        logger.exception("No se pudo iniciar la prueba visual del notch.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
