"""Smoke-test the thin QWidget adapter using QT_QPA_PLATFORM=offscreen only."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from core.eon_state import EonState
from gui.notch_window import NotchController, NotchGeometryState, NotchWindow
from gui.notch_qt import panel_size_for_screen


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_window_geometry_focus_and_visibility(app: QApplication) -> None:
    notch = NotchWindow(NotchController(clock=lambda: 0.0))
    notch.show()
    app.processEvents()
    assert notch.last_error is None
    for geometry in (NotchGeometryState.PEEK, NotchGeometryState.HOVER_PEEK, NotchGeometryState.EXPANDED):
        if geometry == NotchGeometryState.HOVER_PEEK:
            notch.controller.on_mouse_enter()
        elif geometry == NotchGeometryState.EXPANDED:
            notch.controller.on_click()
        notch.refresh()
        notch._geometry_animation.setCurrentTime(notch._geometry_animation.duration())
        app.processEvents()
        screen = QApplication.primaryScreen().geometry()
        expected = panel_size_for_screen(geometry, screen.width(), screen.height())
        assert (notch.width(), notch.height()) == (round(expected.width()), round(expected.height()))
        assert (notch.x(), notch.y()) == (screen.x() + (screen.width() - notch.width()) // 2, screen.y())
        flags = notch.windowFlags()
        assert flags & Qt.WindowType.Tool and flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus) == (geometry != NotchGeometryState.EXPANDED)
        assert notch.character.isVisible() == (geometry == NotchGeometryState.EXPANDED)
    notch.controller.set_eon_state(EonState.ERROR)
    notch.refresh()
    assert notch._status.text() == "Error"
    assert not notch.grab().isNull()
    notch.close()
    assert not notch._timer.isActive()


def test_missing_screen_is_logged_not_fatal(app: QApplication, monkeypatch: pytest.MonkeyPatch,
                                          caplog: pytest.LogCaptureFixture) -> None:
    notch = NotchWindow()
    monkeypatch.setattr(QApplication, "primaryScreen", lambda: None)
    notch.refresh()
    assert notch.last_error and "pantalla" in caplog.text
    notch.close()
