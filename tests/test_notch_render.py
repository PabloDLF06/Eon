"""Check EON's original vector rendering without altering frozen controller tests."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QCheckBox, QPushButton

from core.eon_state import EonState, STATE_LABELS
from gui.char_widget import BADGE_DESIGNS, CharWidget, COLOR_TRANSITION_MS
from gui.notch_qt import CAPSULE_COLOR, REVEAL_LEVELS, panel_size_for_screen, WAVE_DURATION_MS
from gui.notch_window import NotchController, NotchGeometryState, NotchWindow
from scripts.fase4_notch_harness import ControlWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("state", list(EonState))
def test_capsule_edges_remain_dark_for_every_state(app: QApplication, state: EonState) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    notch.controller.expand()
    notch.controller.set_eon_state(state)
    notch.refresh()
    notch._geometry_animation.setCurrentTime(notch._geometry_animation.duration())
    notch._wave_animation.setCurrentTime(WAVE_DURATION_MS)
    notch.character._animation.setCurrentTime(COLOR_TRANSITION_MS)
    notch.character._detail_animation.setCurrentTime(COLOR_TRANSITION_MS)
    notch.show()
    app.processEvents()
    edge = notch.grab().toImage().pixelColor(2, 0)
    assert max(edge.red(), edge.green(), edge.blue()) < 30
    assert notch._status.text() == STATE_LABELS[state]
    assert notch.character._weights[state] == 1.0
    notch.close()


def test_badges_are_complete_and_visually_distinct(app: QApplication) -> None:
    assert set(BADGE_DESIGNS) == set(EonState)
    assert len(set(BADGE_DESIGNS.values())) == 7
    snapshots = set()
    character = CharWidget()
    character.resize(100, 68)
    for state in EonState:
        character.set_eon_state(state)
        character._animation.setCurrentTime(COLOR_TRANSITION_MS)
        character._detail_animation.setCurrentTime(COLOR_TRANSITION_MS)
        image = character.grab().toImage().copy(72, 38, 23, 23)
        snapshots.add(image.constBits().asstring(image.sizeInBytes()))
    assert len(snapshots) == 7
    character.close()


def test_badge_crossfade_preserves_interrupted_mix(app: QApplication) -> None:
    character = CharWidget()
    character.set_eon_state(EonState.THINKING)
    character._detail_animation.setCurrentTime(COLOR_TRANSITION_MS // 2)
    weights = dict(character._weights)
    assert weights[EonState.IDLE] == pytest.approx(0.5)
    assert weights[EonState.THINKING] == pytest.approx(0.5)
    character.set_eon_state(EonState.SPEAKING)
    assert character._source_weights == weights
    character._detail_animation.setCurrentTime(COLOR_TRANSITION_MS)
    assert character._weights[EonState.SPEAKING] == 1.0
    assert sum(character._weights.values()) == pytest.approx(1.0)
    character.close()


def test_reveal_matches_sizes_and_completely_hides_character_when_closed(app: QApplication) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    notch.show()
    for geometry, action in (
        (NotchGeometryState.PEEK, notch.controller.collapse),
        (NotchGeometryState.HOVER_PEEK, notch.controller.on_mouse_enter),
        (NotchGeometryState.EXPANDED, notch.controller.expand),
    ):
        action()
        notch.refresh()
        notch._geometry_animation.setCurrentTime(notch._geometry_animation.duration())
        app.processEvents()
        screen = QApplication.primaryScreen().geometry()
        expected = panel_size_for_screen(geometry, screen.width(), screen.height())
        assert (notch.width(), notch.height()) == (round(expected.width()), round(expected.height()))
        assert notch._reveal == REVEAL_LEVELS[geometry]
        image = notch.grab().toImage()
        if geometry != NotchGeometryState.EXPANDED:
            assert not notch.character.isVisible()
            assert not notch._content.isVisible()
            assert max(image.pixelColor(notch.width() // 2, 4).getRgb()[:3]) < 40
    assert all(effect.opacity() == pytest.approx(1.0) for effect in notch._label_effects)
    notch.close()


def test_reveal_continues_from_current_frame_on_interruption(app: QApplication) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    notch.controller.expand()
    notch.refresh()
    notch._geometry_animation.setCurrentTime(120)
    halfway = notch.panel_size
    notch.controller.collapse()
    notch.refresh()
    assert notch._size_animation.startValue() == halfway
    assert notch._reveal == 0.0
    notch._geometry_animation.setCurrentTime(100)
    halfway = notch.panel_size
    notch.controller.expand()
    notch.refresh()
    assert notch._size_animation.startValue() == halfway
    notch._geometry_animation.setCurrentTime(notch._geometry_animation.duration())
    assert notch._reveal == 1.0
    assert notch.controller.geometry_state == NotchGeometryState.EXPANDED
    notch.close()


def test_harness_seven_states_and_three_flags_remain_operational(app: QApplication) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    controls = ControlWindow(notch)
    buttons = {button.text(): button for button in controls.findChildren(QPushButton)}
    for state in EonState:
        buttons[STATE_LABELS[state]].click()
        assert notch.controller.eon_state == state
    checkboxes = controls.findChildren(QCheckBox)
    assert len(checkboxes) == 3
    for checkbox in checkboxes:
        checkbox.setChecked(True)
    assert notch.controller.voice_active and notch.controller.draft_active and notch.controller.vision_active
    buttons["Colapsar notch"].click()
    assert notch.controller.geometry_state == NotchGeometryState.EXPANDED
    for checkbox in checkboxes:
        checkbox.setChecked(False)
    buttons["Colapsar notch"].click()
    assert notch.controller.geometry_state == NotchGeometryState.PEEK
    buttons["Expandir notch"].click()
    assert notch.controller.geometry_state == NotchGeometryState.EXPANDED
    controls.close()
    notch.close()
