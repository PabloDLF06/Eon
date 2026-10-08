"""Verify strict persisted settings, painted controls and mocked peripheral operations."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from unittest.mock import Mock

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtCore import QRect, QThread
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QColorDialog, QFileDialog

import config
from gui import notch_controls
from gui.notch_controls import IconButton, MicrophoneLevelWorker, SettingsDialog, launch_shortcut


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = deepcopy(config._SETTINGS)
    data["quick_launch_shortcuts"] = []
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_PATH", path)
    monkeypatch.setattr(config, "_SETTINGS", data)
    return path


@pytest.mark.parametrize("shortcuts", [None, {}, "x", [None], [{}],
    [{"label": "App", "path": "relative.exe", "color": "#8ea9c7"}],
    [{"label": "", "path": "C:/app.exe", "color": "#8ea9c7"}],
    [{"label": " App", "path": "C:/app.exe", "color": "#8ea9c7"}],
    [{"label": "App\n", "path": "C:/app.exe", "color": "#8ea9c7"}],
    [{"label": True, "path": "C:/app.exe", "color": "#8ea9c7"}],
    [{"label": "App", "path": "C:/app.exe", "color": "red"}],
    [{"label": "App", "path": "C:/app.exe", "color": "#123"}],
    [{"label": "App", "path": "C:/app.exe", "color": "#GGGGGG"}],
    [{"label": "App", "path": "C:/app.exe", "color": "#123456", "command": "x"}],
    [{"label": "App", "path": None, "color": "#123456"}]])
def test_reject_invalid_shortcuts(shortcuts: object, settings_file: Path) -> None:
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    data["quick_launch_shortcuts"] = shortcuts
    settings_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match="quick_launch_shortcuts"):
        config._load_settings(settings_file)


def test_shortcuts_required(settings_file: Path) -> None:
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    del data["quick_launch_shortcuts"]
    settings_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match="quick_launch_shortcuts"):
        config._load_settings(settings_file)


def test_save_preserves_other_fields_and_returns_copies(settings_file: Path, tmp_path: Path) -> None:
    original = json.loads(settings_file.read_text(encoding="utf-8"))
    original["custom_metadata"] = {"note": "Se conserva"}
    settings_file.write_text(json.dumps(original), encoding="utf-8")
    shortcuts = [{"label": "Carpeta", "path": str(tmp_path), "color": "#ABcd12"}]
    config.save_notch_settings(auto_hide_enabled=False, auto_hide_seconds=7, shortcuts=shortcuts)
    saved = config._load_settings(settings_file)
    for field in original.keys() - {"notch_auto_hide_enabled", "notch_auto_hide_seconds", "quick_launch_shortcuts"}:
        assert saved[field] == original[field]
    assert saved["quick_launch_shortcuts"] == shortcuts
    assert config.get_notch_settings() == {"notch_auto_hide_enabled": False, "notch_auto_hide_seconds": 7}
    shortcuts[0]["label"] = "changed"
    config.get_quick_launch_shortcuts()[0]["label"] = "also changed"
    assert config.get_quick_launch_shortcuts()[0]["label"] == "Carpeta"
    assert not list(settings_file.parent.glob(".eon-settings-*"))


@pytest.mark.parametrize("enabled,seconds", [(1, 10), (True, False), (True, 0), (False, 1.5)])
def test_invalid_save_preserves_file_and_cache(enabled: object, seconds: object, settings_file: Path) -> None:
    before, cache = settings_file.read_bytes(), deepcopy(config._SETTINGS)
    with pytest.raises(config.ConfigurationError):
        config.save_notch_settings(auto_hide_enabled=enabled, auto_hide_seconds=seconds, shortcuts=[])
    assert settings_file.read_bytes() == before and config._SETTINGS == cache


def test_failed_publication_retains_old_file_and_cleans_temporary(settings_file: Path,
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    before, cache = settings_file.read_bytes(), deepcopy(config._SETTINGS)
    monkeypatch.setattr(config.os, "replace", Mock(side_effect=OSError("disco")))
    with pytest.raises(config.ConfigurationError):
        config.save_notch_settings(auto_hide_enabled=False, auto_hide_seconds=9, shortcuts=[])
    assert settings_file.read_bytes() == before and config._SETTINGS == cache
    assert not list(settings_file.parent.glob(".eon-settings-*"))
    assert "guardar" in caplog.text


@pytest.mark.parametrize("application", [True, False])
def test_launch_uses_argv_without_real_process(application: bool, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "app with spaces.exe" if application else tmp_path
    if application:
        target.touch()
    popen = Mock()
    monkeypatch.setattr(notch_controls.subprocess, "Popen", popen)
    assert launch_shortcut(str(target))[0]
    expected = [str(target)] if application else ["explorer.exe" if os.name == "nt" else "xdg-open", str(target)]
    popen.assert_called_once_with(expected, shell=False)


def test_missing_path_and_launch_failure_are_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    popen = Mock(side_effect=OSError("permiso"))
    monkeypatch.setattr(notch_controls.subprocess, "Popen", popen)
    assert not launch_shortcut(str(tmp_path / "missing.exe"))[0]
    popen.assert_not_called()
    ok, message = launch_shortcut(str(tmp_path))
    assert not ok and "No se pudo" in message and "acceso directo" in caplog.text


def test_dialog_add_choose_color_save_and_remove(app: QApplication, settings_file: Path,
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dialog = SettingsDialog()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(tmp_path / "app.exe"), ""))
    monkeypatch.setattr(QColorDialog, "getColor", lambda *args: QColor("#39ff88"))
    dialog.add_shortcut()
    dialog.choose_file()
    dialog.choose_color()
    assert dialog.cards[0].label.text() == "app"
    dialog.enabled.setChecked(False)
    dialog.seconds.setValue(5)
    saved = Mock()
    dialog.saved.connect(saved)
    dialog.save()
    saved.assert_called_once()
    assert config.get_quick_launch_shortcuts() == [{"label": "app", "path": str(tmp_path / "app.exe"), "color": "#39ff88"}]
    second = SettingsDialog()
    second._selected_card = second.cards[0]
    second.remove_shortcut()
    second.save()
    assert config.get_quick_launch_shortcuts() == []
    dialog.close()
    second.close()


def test_dialog_rejects_incomplete_row_without_closing(app: QApplication, settings_file: Path) -> None:
    dialog = SettingsDialog()
    dialog.add_shortcut()
    before = settings_file.read_bytes()
    dialog.save()
    assert "No se pudo guardar" in dialog.error.text()
    assert dialog.result() != dialog.DialogCode.Accepted
    assert settings_file.read_bytes() == before
    dialog.close()


@pytest.mark.parametrize("kind", ["gear", "microphone", "send"])
def test_original_icons_render_with_accessible_labels(app: QApplication, kind: str) -> None:
    button = IconButton(kind, "Acción local")
    assert button.accessibleName() == "Acción local"
    assert not button.grab().isNull()
    button.close()


def test_worker_uses_background_thread_and_delivers_real_detector_levels(app: QApplication) -> None:
    ids, levels, results = [], [], []
    def capture(seconds: float) -> float:
        ids.append(threading.get_ident())
        assert 0 < seconds <= 0.12
        time.sleep(0.004)
        return 0.04
    detector = Mock(last_error=None, get_input_level=Mock(side_effect=capture))
    worker = MicrophoneLevelWorker(duration=0.015, detector_factory=lambda: detector)
    worker.level.connect(levels.append)
    worker.result.connect(results.append)
    worker.start()
    assert worker.wait(1000)
    app.processEvents()
    assert levels and set(levels) == {0.04}
    assert all(identifier != threading.get_ident() for identifier in ids)
    assert "No se ha transcrito" in results[0]


@pytest.mark.parametrize("error,level", [("dispositivo", 0.0), (None, float("nan")), (None, 2.0)])
def test_meter_errors_logged_not_propagated(app: QApplication, error: str | None, level: float,
        caplog: pytest.LogCaptureFixture) -> None:
    detector = Mock(last_error=error, get_input_level=Mock(return_value=level))
    worker = MicrophoneLevelWorker(duration=0.01, detector_factory=lambda: detector)
    results = []
    worker.result.connect(results.append)
    worker.run()
    assert results and "No se pudo" in results[0]
    assert "micrófono" in caplog.text


def test_meter_cancel_prevents_capture(app: QApplication) -> None:
    detector = Mock()
    worker = MicrophoneLevelWorker(detector_factory=lambda: detector)
    worker.stop()
    worker.run()
    detector.get_input_level.assert_not_called()


def test_dark_cards_presets_keep_custom_seconds(app: QApplication, settings_file: Path) -> None:
    config._SETTINGS["notch_auto_hide_seconds"] = 7
    dialog = SettingsDialog()
    assert dialog.seconds.value() == 7 and dialog.presets.currentData() is None
    assert dialog.size().width() <= 520 and dialog.size().height() <= 560
    assert "#121318" in dialog.styleSheet()
    dialog.presets.setCurrentIndex(dialog.presets.findData(20))
    assert dialog.seconds.value() == 20
    dialog.add_shortcut()
    assert len(dialog.cards) == 1
    dialog.cards[0].path = "C:/very/long/path/" + "segment/" * 20 + "app.exe"
    dialog.cards[0]._refresh_data()
    assert dialog.cards[0].path_label.toolTip() == dialog.cards[0].path
    dialog.close()


def test_card_actions_choose_directory_and_delete_only_that_card(app: QApplication,
        settings_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dialog = SettingsDialog()
    dialog.add_shortcut()
    first = dialog.cards[0]
    dialog.add_shortcut()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    first._select_and_call(dialog.choose_directory)
    assert first.path == str(tmp_path) and first.label.text() == tmp_path.stem
    first._select_and_call(dialog.remove_shortcut)
    assert len(dialog.cards) == 1 and dialog.cards[0] is not first
    dialog.close()


def test_continuous_worker_does_not_stop_at_old_three_second_deadline(app: QApplication,
        monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(notch_controls.time, "monotonic", lambda: now[0])
    worker = MicrophoneLevelWorker()
    def capture(seconds: float) -> float:
        assert seconds == 0.12
        now[0] += 1
        if now[0] == 6:
            worker.stop()
        return 0.01
    detector = Mock(last_error=None, get_input_level=Mock(side_effect=capture))
    worker._detector_factory = lambda: detector
    levels = []
    worker.level.connect(levels.append)
    worker.run()
    assert len(levels) == 6 and worker.last_error is None


@pytest.mark.parametrize("duration", [True, 0, -1, float("inf"), float("nan"), "3"])
def test_worker_rejects_invalid_duration(app: QApplication, duration: object) -> None:
    with pytest.raises(ValueError):
        MicrophoneLevelWorker(duration=duration)


def test_dialog_clamps_to_small_screen_without_native_minimum_expanding_it(app: QApplication,
        settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SettingsDialog, "screen", lambda self: Mock(availableGeometry=lambda: QRect(0, 0, 400, 450)))
    dialog = SettingsDialog()
    dialog.show()
    app.processEvents()
    assert dialog.width() <= 376 and dialog.height() <= 426
    dialog.close()
