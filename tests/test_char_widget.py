"""Check real Qt color interpolation offscreen, without pytest-qt or desktop access."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from core.eon_state import EonState, STATE_LABELS, state_color
from gui.char_widget import CharWidget, COLOR_TRANSITION_MS


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication([])
    return application


def test_character_interpolates_and_handles_interrupted_animation(app: QApplication) -> None:
    character = CharWidget()
    character.show()
    app.processEvents()
    assert character.display_color.name() == state_color(EonState.IDLE)
    character.set_eon_state(EonState.SPEAKING)
    assert character.display_color.name() == state_color(EonState.IDLE)
    character._animation.setCurrentTime(COLOR_TRANSITION_MS // 2)
    midpoint = character.display_color
    assert midpoint not in (QColor(state_color(EonState.IDLE)), QColor(state_color(EonState.SPEAKING)))
    character.set_eon_state(EonState.THINKING)
    assert character._animation.startValue() == midpoint
    character._animation.setCurrentTime(COLOR_TRANSITION_MS)
    assert character.display_color.name() == state_color(EonState.THINKING)
    assert character.accessibleDescription() == STATE_LABELS[EonState.THINKING]
    assert not character.grab().isNull()
    character.close()


def test_character_rejects_invalid_state(app: QApplication) -> None:
    character = CharWidget()
    with pytest.raises(ValueError):
        character.set_eon_state("idle")
    character.close()
