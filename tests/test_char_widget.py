"""Check real Qt color interpolation offscreen, without pytest-qt or desktop access."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtGui import QColor
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
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


def test_idle_glance_and_state_squash_lifecycle(app: QApplication) -> None:
    character = CharWidget()
    character.show()
    app.processEvents()
    assert 4000 <= character._glance_timer.interval() <= 9000
    character._start_glance()
    character._glance_animation.setCurrentTime(260)
    assert abs(character.glance_offset) == 2
    character._glance_animation.setCurrentTime(940)
    assert character.glance_offset == 0
    character.set_eon_state(EonState.LISTENING)
    assert not character._glance_timer.isActive()
    character._squash_animation.setCurrentTime(80)
    assert character.squash_amount == 1
    character.hide()
    assert not character._glance_timer.isActive()
    assert character.squash_amount == character.recoil_amount == 0
    character.close()


def test_all_artwork_fits_even_at_maximum_motion(app: QApplication) -> None:
    import math
    character = CharWidget()
    character.breath_phase = math.pi
    character.squash_amount = 1
    character.recoil_amount = 1
    image = QImage(120, 76, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    character._paint_artwork(painter, QRectF(0, 0, 120, 76))
    painter.end()
    for x in range(image.width()):
        assert image.pixelColor(x, 0).alpha() == image.pixelColor(x, 75).alpha() == 0
    for y in range(image.height()):
        assert image.pixelColor(0, y).alpha() == image.pixelColor(119, y).alpha() == 0
    assert image.pixelColor(60, 30).alpha() > 0
    character.close()
