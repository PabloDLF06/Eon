"""Check EON's original vector rendering without altering frozen controller tests."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QCheckBox, QPushButton

from core.eon_state import EonState, STATE_LABELS
from gui.char_widget import BADGE_DESIGNS, CharWidget, COLOR_TRANSITION_MS
from gui.notch_qt import CAPSULE_COLOR, PANEL_SIZES, REVEAL_LEVELS
from gui.notch_window import NotchController, NotchGeometryState, NotchWindow
from scripts.fase4_notch_harness import ControlWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("state", list(EonState))
def test_capsule_stays_neutral_for_every_state(app: QApplication, state: EonState) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    notch.controller.expand()
    notch.controller.set_eon_state(state)
    notch.refresh()
    notch._reveal_animation.setCurrentTime(COLOR_TRANSITION_MS)
    notch.character._animation.setCurrentTime(COLOR_TRANSITION_MS)
    notch.character._detail_animation.setCurrentTime(COLOR_TRANSITION_MS)
    notch.show()
    app.processEvents()
    assert notch.grab().toImage().pixelColor(15, 10).name() == CAPSULE_COLOR
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


def test_reveal_preserves_native_sizes_and_shows_crest(app: QApplication) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    notch.show()
    for geometry, action in (
        (NotchGeometryState.PEEK, notch.controller.collapse),
        (NotchGeometryState.HOVER_PEEK, notch.controller.on_mouse_enter),
        (NotchGeometryState.EXPANDED, notch.controller.expand),
    ):
        action()
        notch.refresh()
        notch._reveal_animation.setCurrentTime(COLOR_TRANSITION_MS)
        app.processEvents()
        assert (notch.width(), notch.height()) == PANEL_SIZES[geometry]
        assert notch._reveal == REVEAL_LEVELS[geometry]
        image = notch.grab().toImage()
        if geometry == NotchGeometryState.PEEK:
            assert image.pixelColor(notch.width() // 2, 4).red() > 130
        else:
            assert image.height() > 5
    assert all(effect.opacity() == pytest.approx(1.0) for effect in notch._label_effects)
    notch.close()


def test_reveal_continues_from_current_frame_on_interruption(app: QApplication) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    notch.controller.on_mouse_enter()
    notch.refresh()
    notch._reveal_animation.setCurrentTime(COLOR_TRANSITION_MS // 2)
    halfway = notch._reveal
    notch.controller.expand()
    notch.refresh()
    assert notch._reveal_animation.startValue() == pytest.approx(halfway)
    notch._reveal_animation.setCurrentTime(COLOR_TRANSITION_MS)
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
