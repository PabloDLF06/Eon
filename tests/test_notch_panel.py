"""Check the expanded-panel contract, native animation and independent radial track."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
import time
from unittest.mock import Mock

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import config
from core.eon_state import EonState
from gui.char_widget import CharWidget
from gui import notch_qt
from gui.notch_qt import (ACKNOWLEDGEMENT, NotchWindow, WAVE_DURATION_MS,
                          panel_size_for_screen)
from gui.notch_window import NotchController, NotchGeometryState


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def notch(app: QApplication):
    window = NotchWindow(NotchController(clock=lambda: 0.0))
    window.controller.expand()
    window.refresh()
    window._geometry_animation.setCurrentTime(window._geometry_animation.duration())
    window.show()
    app.processEvents()
    yield window
    window.close()


@pytest.mark.parametrize("text", ["", " ", "\n\t"])
def test_empty_message_has_no_side_effects(notch: NotchWindow, text: str) -> None:
    notch.text_input.setText(text)
    message = notch._message.text()
    notch.handle_user_message(text)
    assert notch.text_input.text() == text
    assert notch._message.text() == message


@pytest.mark.parametrize("send", ["button", "enter", "direct"])
def test_nonempty_message_cleared_and_honestly_acknowledged(notch: NotchWindow, send: str) -> None:
    notch.text_input.setText("Hola, Eon")
    assert notch.controller.draft_active
    if send == "button":
        notch.send_button.click()
    elif send == "enter":
        QTest.keyClick(notch.text_input, Qt.Key.Key_Return)
    else:
        notch.handle_user_message("Hola, Eon")
    assert notch.text_input.text() == ""
    assert not notch.controller.draft_active
    assert notch._message.text() == ACKNOWLEDGEMENT


def test_nontext_message_rejected(notch: NotchWindow) -> None:
    with pytest.raises(ValueError):
        notch.handle_user_message(None)


def test_empty_shortcuts_not_an_error(notch: NotchWindow) -> None:
    assert notch.shortcut_buttons == []
    assert not notch._shortcuts_scroll.isVisible()
    assert notch.last_error is None


def test_shortcut_click_updates_panel_without_real_process(notch: NotchWindow,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "get_quick_launch_shortcuts", lambda: [
        {"label": "Carpeta", "path": "C:/example", "color": "#00e5ff"}])
    launcher = Mock(return_value=(False, "No se pudo abrir la ruta."))
    monkeypatch.setattr(notch_qt, "launch_shortcut", launcher)
    notch._rebuild_shortcuts()
    notch.shortcut_buttons[0].click()
    launcher.assert_called_once_with("C:/example")
    assert "No se pudo" in notch._message.text()


def test_settings_opens_single_real_dialog_and_applies_live(notch: NotchWindow,
        monkeypatch: pytest.MonkeyPatch) -> None:
    notch.open_settings()
    dialog = notch._settings_dialog
    notch.open_settings()
    assert notch._settings_dialog is dialog and dialog.isVisible()
    monkeypatch.setattr(config, "get_notch_settings", lambda: {
        "notch_auto_hide_enabled": False, "notch_auto_hide_seconds": 4})
    monkeypatch.setattr(config, "get_quick_launch_shortcuts", lambda: [
        {"label": "Nuevo", "path": "C:/example", "color": "#a742ff"}])
    notch._apply_settings()
    assert not notch.controller._settings["notch_auto_hide_enabled"]
    assert notch.shortcut_buttons[0].text() == "Nuevo"
    dialog.reject()
    assert notch._settings_dialog is None


@pytest.mark.parametrize("width,closed", [(1366, 280), (1920, 300), (2560, 320)])
def test_screen_proportional_sizes(width: int, closed: int) -> None:
    assert panel_size_for_screen(NotchGeometryState.PEEK, width, 1080).width() == closed
    assert panel_size_for_screen(NotchGeometryState.HOVER_PEEK, width, 1080).height() == 27
    assert panel_size_for_screen(NotchGeometryState.EXPANDED, width, 1080).width() == 460


def test_small_screen_clamps_instead_of_extending_outside() -> None:
    size = panel_size_for_screen(NotchGeometryState.EXPANDED, 400, 200)
    assert size.width() <= 400 and size.height() <= 200


def test_actual_native_size_animates_in_parallel_and_stays_edge_anchored(notch: NotchWindow) -> None:
    notch.controller.collapse()
    notch.refresh()
    assert notch._reveal == 0.0 and not notch.character.isVisible()
    notch._geometry_animation.setCurrentTime(notch._geometry_animation.duration())
    before = notch.size()
    notch.controller.expand()
    notch.refresh()
    assert notch.size() == before
    assert notch._geometry_animation.animationCount() == 3
    notch._geometry_animation.setCurrentTime(90)
    assert before.height() < notch.height() < 280
    assert 0 < notch._reveal < 1 and 0 < notch._content_effect.opacity() < 1
    assert notch.y() == notch._screen_area.y()
    notch._geometry_animation.setCurrentTime(notch._geometry_animation.duration())
    assert notch.height() == 280 and notch.width() == 460


def test_wave_does_not_restart_on_geometry_change(notch: NotchWindow) -> None:
    notch.controller.set_eon_state(EonState.THINKING)
    notch.refresh()
    notch._wave_animation.setCurrentTime(180)
    progress, current = notch._wave_progress, notch._wave_animation.currentTime()
    for action in (notch.controller.collapse, notch.controller.on_mouse_enter, notch.controller.expand):
        action()
        notch.refresh()
        assert notch._wave_animation.currentTime() == current
        assert notch._wave_progress == progress


def test_radial_front_changes_center_before_distant_pixels(notch: NotchWindow) -> None:
    notch.controller.collapse()
    notch.refresh()
    notch._geometry_animation.setCurrentTime(notch._geometry_animation.duration())
    notch.controller.set_eon_state(EonState.LISTENING)
    notch.refresh()
    notch.wave_progress = 0.2
    early = notch.grab().toImage()
    notch.wave_progress = 0.8
    later = notch.grab().toImage()
    middle, far, y = notch.width() // 2, int(notch.width() * 0.77), 6
    assert early.pixelColor(middle, y) != later.pixelColor(middle, y)
    assert early.pixelColor(far, y) != later.pixelColor(far, y)
    assert early.pixelColor(0, 0) == later.pixelColor(0, 0)
    assert later.pixelColor(0, 0).alpha() == 255
    assert later.pixelColor(0, notch.height() - 1).alpha() < 255


def test_breath_blink_and_hidden_lifecycle(app: QApplication) -> None:
    character = CharWidget()
    character.show()
    app.processEvents()
    assert character._breath_animation.state() == character._breath_animation.State.Running
    character.breath_phase = math.pi
    assert character._breath_phase == math.pi
    assert 3000 <= character._blink_timer.interval() <= 7000
    character._start_blink()
    character._blink_animation.setCurrentTime(85)
    assert character.blink_progress == 1.0
    character._blink_animation.setCurrentTime(235)
    assert character.blink_progress == 0.0
    character.set_eon_state(EonState.THINKING)
    assert character._breath_animation.state() == character._breath_animation.State.Stopped
    character.hide()
    assert not character._blink_timer.isActive()
    assert character._blink_animation.state() == character._blink_animation.State.Stopped
    character.close()


def test_reload_keeps_flags_state_and_full_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    controller = NotchController(clock=lambda: now[0])
    controller.expand()
    controller.notify_vision_active(True)
    monkeypatch.setattr(config, "get_notch_settings", lambda: {
        "notch_auto_hide_enabled": True, "notch_auto_hide_seconds": 5})
    controller.reload_settings()
    assert controller.vision_active and controller.geometry_state == NotchGeometryState.EXPANDED
    controller.notify_vision_active(False)
    now[0] = 4.9
    controller.tick()
    assert controller.geometry_state == NotchGeometryState.EXPANDED
    controller.reload_settings()
    now[0] = 9.8
    controller.tick()
    assert controller.geometry_state == NotchGeometryState.EXPANDED
    now[0] = 9.9
    controller.tick()
    assert controller.geometry_state == NotchGeometryState.PEEK


def test_microphone_button_wires_mocked_capture_and_restores_activity(notch: NotchWindow,
        app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from gui.notch_controls import MicrophoneLevelWorker
    def capture(seconds: float) -> float:
        time.sleep(0.005)
        return 0.04
    detector = Mock(last_error=None, get_input_level=Mock(side_effect=capture))
    created = []
    def factory(parent: object) -> MicrophoneLevelWorker:
        worker = MicrophoneLevelWorker(parent, duration=0.03, detector_factory=lambda: detector)
        created.append(worker)
        return worker
    monkeypatch.setattr(notch_qt, "MicrophoneLevelWorker", factory)
    observed = []
    notch._meter.valueChanged.connect(observed.append)
    notch.microphone_button.click()
    assert notch.controller.voice_active and not notch.microphone_button.isEnabled()
    notch.start_microphone_meter()
    assert len(created) == 1
    for _ in range(100):
        QTest.qWait(10)
        if notch._microphone is None:
            break
    assert any(value > 0 for value in observed)
    assert notch._microphone is None
    assert not notch.controller.voice_active and notch.microphone_button.isEnabled()
    assert "No se ha transcrito" in notch._message.text()


def test_close_requests_meter_cancel_and_defers_destruction(notch: NotchWindow,
        app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from gui.notch_controls import MicrophoneLevelWorker
    def capture(seconds: float) -> float:
        time.sleep(0.03)
        return 0.01
    detector = Mock(last_error=None, get_input_level=Mock(side_effect=capture))
    worker = MicrophoneLevelWorker(notch, duration=1.0, detector_factory=lambda: detector)
    monkeypatch.setattr(notch_qt, "MicrophoneLevelWorker", lambda parent: worker)
    notch.start_microphone_meter()
    notch.close()
    assert notch._close_pending and worker._cancel.is_set()
    assert not notch._closed
    for _ in range(100):
        QTest.qWait(10)
        if notch._closed:
            break
    assert notch._closed and notch._microphone is None and not notch._timer.isActive()


def test_meter_start_failure_preserves_previous_voice_flag(notch: NotchWindow,
        monkeypatch: pytest.MonkeyPatch) -> None:
    notch.controller.notify_voice_activity(True)
    monkeypatch.setattr(notch_qt, "MicrophoneLevelWorker", Mock(side_effect=RuntimeError("hilo")))
    notch.start_microphone_meter()
    assert notch.controller.voice_active
    assert "No se pudo" in notch._message.text()
    assert notch._microphone is None and notch.microphone_button.isEnabled()


def test_all_content_stays_inside_padding_with_shortcuts(notch: NotchWindow,
        app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "get_quick_launch_shortcuts", lambda: [
        {"label": "Aplicación", "path": "C:/example", "color": "#8ea9c7"}])
    notch._rebuild_shortcuts()
    app.processEvents()
    for child in (notch.character, notch._message, notch.text_input,
                  notch.microphone_button, notch.send_button, notch.settings_button):
        point = child.mapTo(notch, child.rect().topLeft())
        assert point.x() >= 18 and point.y() >= 18
        assert point.x() + child.width() <= notch.width() - 18
        assert point.y() + child.height() <= notch.height() - 18


def test_keyboard_order_follows_visible_panel(notch: NotchWindow,
        app: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "get_quick_launch_shortcuts", lambda: [
        {"label": "Aplicación", "path": "C:/example", "color": "#8ea9c7"}])
    notch._rebuild_shortcuts()
    notch.settings_button.setFocus()
    QTest.keyClick(notch.settings_button, Qt.Key.Key_Tab)
    assert app.focusWidget() is notch.shortcut_buttons[0]
    QTest.keyClick(notch.shortcut_buttons[0], Qt.Key.Key_Tab)
    assert app.focusWidget() is notch.text_input
