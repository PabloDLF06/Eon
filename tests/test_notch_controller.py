"""Test pure controller events and timing without Qt or a QApplication."""

from collections.abc import Callable
from pathlib import Path
import subprocess
import sys

import pytest

import config
from core.eon_state import EonState
from gui.notch_window import NotchController, NotchGeometryState


class Clock:
    """Expose deterministic monotonic time without real sleeping."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch) -> tuple[NotchController, Clock]:
    monkeypatch.setattr(config, "get_notch_settings", lambda: {
        "notch_auto_hide_enabled": True, "notch_auto_hide_seconds": 10,
    })
    clock = Clock()
    return NotchController(clock=clock), clock


def test_idle_hides_at_exact_deadline(controller: tuple[NotchController, Clock]) -> None:
    notch, clock = controller
    notch.on_click()
    clock.advance(9.99)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    clock.advance(0.01)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.PEEK


def test_hover_and_click_transitions(controller: tuple[NotchController, Clock]) -> None:
    notch, clock = controller
    notch.on_mouse_enter()
    assert notch.geometry_state == NotchGeometryState.HOVER_PEEK
    notch.on_mouse_leave()
    assert notch.geometry_state == NotchGeometryState.PEEK
    notch.on_mouse_enter()
    notch.on_click()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    clock.advance(100)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    notch.on_mouse_leave()
    clock.advance(10)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.PEEK


@pytest.mark.parametrize("method", ["notify_voice_activity", "notify_draft_activity", "notify_vision_active"])
def test_activity_blocks_and_restarts_idle(controller: tuple[NotchController, Clock], method: str) -> None:
    notch, clock = controller
    notch.expand()
    clock.advance(9)
    notify: Callable[[bool], None] = getattr(notch, method)
    notify(True)
    clock.advance(100)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    notify(False)
    clock.advance(9)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    clock.advance(1)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.PEEK


@pytest.mark.parametrize("state", [state for state in EonState if state != EonState.IDLE])
def test_nonidle_blocks_hiding(controller: tuple[NotchController, Clock], state: EonState) -> None:
    notch, clock = controller
    notch.expand()
    notch.set_eon_state(state)
    clock.advance(100)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    notch.set_eon_state(EonState.IDLE)
    clock.advance(10)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.PEEK


def test_vision_forces_expansion_and_defeats_collapse(controller: tuple[NotchController, Clock]) -> None:
    notch, clock = controller
    assert notch.geometry_state == NotchGeometryState.PEEK
    notch.notify_vision_active(True)
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    notch.collapse()
    notch.on_mouse_enter()
    notch.on_mouse_leave()
    clock.advance(100)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    assert notch.eon_state == EonState.IDLE and notch.vision_active


def test_multiple_flags_require_all_to_finish(controller: tuple[NotchController, Clock]) -> None:
    notch, clock = controller
    notch.expand()
    notch.notify_voice_activity(True)
    notch.notify_draft_activity(True)
    notch.notify_voice_activity(False)
    clock.advance(100)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    notch.notify_draft_activity(False)
    clock.advance(10)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.PEEK


def test_repeated_notifications_do_not_extend_deadline(controller: tuple[NotchController, Clock]) -> None:
    notch, clock = controller
    notch.expand()
    clock.advance(9)
    notch.notify_voice_activity(False)
    notch.set_eon_state(EonState.IDLE)
    clock.advance(1)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.PEEK


def test_disabled_autohide_still_allows_manual_collapse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "get_notch_settings", lambda: {
        "notch_auto_hide_enabled": False, "notch_auto_hide_seconds": 10,
    })
    clock = Clock()
    notch = NotchController(clock=clock)
    notch.expand()
    clock.advance(1000)
    notch.tick()
    assert notch.geometry_state == NotchGeometryState.EXPANDED
    notch.collapse()
    assert notch.geometry_state == NotchGeometryState.PEEK
    notch.notify_vision_active(True)
    assert notch.geometry_state == NotchGeometryState.EXPANDED


@pytest.mark.parametrize("method", ["notify_voice_activity", "notify_draft_activity", "notify_vision_active"])
@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_flags_require_bool(controller: tuple[NotchController, Clock], method: str, value: object) -> None:
    with pytest.raises(ValueError):
        getattr(controller[0], method)(value)


def test_read_only_and_invalid_state(controller: tuple[NotchController, Clock]) -> None:
    notch, clock = controller
    with pytest.raises(AttributeError):
        notch.geometry_state = NotchGeometryState.EXPANDED
    with pytest.raises(ValueError):
        notch.set_eon_state("idle")
    clock.value = -1
    with pytest.raises(ValueError, match="monótono"):
        notch.tick()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "1"])
def test_invalid_clock_value(value: object) -> None:
    with pytest.raises(ValueError):
        NotchController(clock=lambda: value)


def test_logic_imports_and_runs_even_when_pyqt_imports_are_forbidden() -> None:
    code = """
import importlib.abc
import sys
class BlockQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'PyQt6' or fullname.startswith('PyQt6.'):
            raise RuntimeError('Qt imports forbidden in pure logic')
sys.meta_path.insert(0, BlockQt())
from core.eon_state import EonState
from gui.notch_window import NotchController, NotchGeometryState
controller = NotchController(clock=lambda: 0.0)
controller.set_eon_state(EonState.LISTENING)
controller.on_click()
controller.tick()
assert controller.geometry_state == NotchGeometryState.EXPANDED
assert not any(name.startswith('PyQt6') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
