"""Keep notch timing pure; load the QWidget adapter only when explicitly requested."""

from collections.abc import Callable
from enum import Enum
import math
import time
from typing import Any, TYPE_CHECKING

import config
from core.eon_state import EonState

if TYPE_CHECKING:
    from gui.notch_qt import NotchWindow


class NotchGeometryState(Enum):
    """Describe panel geometry independently of the assistant's activity."""

    PEEK = "peek"
    HOVER_PEEK = "hover_peek"
    EXPANDED = "expanded"


class NotchController:
    """Advance geometry with continuous idle time, never with toolkit timers.

    Activity or pointer presence resets the idle deadline. Releasing a blocker
    starts a fresh full interval; blocked time is never counted retroactively.
    Vision expansion outranks clicks, hover and manual collapse.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        if not callable(clock):
            raise ValueError("El reloj debe ser una función invocable.")
        self._clock = clock
        self._settings = config.get_notch_settings()
        self._eon_state = EonState.IDLE
        self._geometry_state = NotchGeometryState.PEEK
        self._voice_active = False
        self._draft_active = False
        self._vision_active = False
        self._mouse_inside = False
        self._idle_since: float | None = None
        self._last_time: float | None = None
        self._now()

    @property
    def eon_state(self) -> EonState:
        """Expose logical activity without allowing direct assignment."""
        return self._eon_state

    @property
    def geometry_state(self) -> NotchGeometryState:
        """Expose the current geometry; changes go through controller events."""
        return self._geometry_state

    @property
    def voice_active(self) -> bool:
        """Expose the independently notified voice activity flag."""
        return self._voice_active

    @property
    def draft_active(self) -> bool:
        """Expose the independently notified draft activity flag."""
        return self._draft_active

    @property
    def vision_active(self) -> bool:
        """Expose the vision flag that forces expanded geometry."""
        return self._vision_active

    def _now(self) -> float:
        """Reject invalid/backward injected clocks rather than miscounting idle."""
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("El reloj debe devolver segundos finitos.")
        if self._last_time is not None and value < self._last_time:
            raise ValueError("El reloj del notch debe ser monótono.")
        self._last_time = value
        return value

    def _eligible(self) -> bool:
        """Count idle only for a visible panel with no activity or pointer."""
        return bool(self._settings["notch_auto_hide_enabled"]) and (
            self._geometry_state != NotchGeometryState.PEEK
            and self._eon_state == EonState.IDLE
            and not any((self._voice_active, self._draft_active, self._vision_active, self._mouse_inside))
        )

    def _reset_deadline(self) -> None:
        """Start a fresh idle interval only when all blockers are absent."""
        now = self._now()
        self._idle_since = now if self._eligible() else None

    def set_eon_state(self, state: EonState) -> None:
        """Set logical activity without deriving any geometry or other flags."""
        if not isinstance(state, EonState):
            raise ValueError("El estado debe ser un valor de EonState.")
        if state != self._eon_state:
            self._eon_state = state
            self._reset_deadline()

    def _notify_activity(self, attribute: str, active: bool) -> None:
        """Validate a flag and reset timing only when its value changes."""
        if type(active) is not bool:
            raise ValueError("La actividad debe indicarse con True o False.")
        if getattr(self, attribute) != active:
            setattr(self, attribute, active)
            if self._vision_active:
                self._geometry_state = NotchGeometryState.EXPANDED
            self._reset_deadline()

    def notify_voice_activity(self, active: bool) -> None:
        """Block auto-hide during notified voice activity without changing state."""
        self._notify_activity("_voice_active", active)

    def notify_draft_activity(self, active: bool) -> None:
        """Block auto-hide while a draft is active without changing state."""
        self._notify_activity("_draft_active", active)

    def notify_vision_active(self, active: bool) -> None:
        """Force expansion during vision and restart idle timing on release."""
        self._notify_activity("_vision_active", active)

    def on_mouse_enter(self) -> None:
        """Reveal a preview from peek, leaving expanded geometry unchanged."""
        self._mouse_inside = True
        if self._geometry_state == NotchGeometryState.PEEK:
            self._geometry_state = NotchGeometryState.HOVER_PEEK
        self._reset_deadline()

    def on_mouse_leave(self) -> None:
        """Dismiss an unclicked preview and restart expanded idle timing."""
        self._mouse_inside = False
        if self._geometry_state == NotchGeometryState.HOVER_PEEK:
            self._geometry_state = NotchGeometryState.PEEK
        self._reset_deadline()

    def on_click(self) -> None:
        """Expand on click without changing assistant activity."""
        self.expand()

    def expand(self) -> None:
        """Allow the manual harness to expand without inventing mouse events."""
        self._geometry_state = NotchGeometryState.EXPANDED
        self._reset_deadline()

    def collapse(self) -> None:
        """Collapse manually unless vision currently requires expansion."""
        if not self._vision_active:
            self._geometry_state = NotchGeometryState.PEEK
        self._reset_deadline()

    def tick(self) -> None:
        """Enforce vision priority and expire a continuous eligible idle interval."""
        now = self._now()
        if self._vision_active:
            self._geometry_state = NotchGeometryState.EXPANDED
        if not self._eligible():
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = now
        elif now - self._idle_since >= self._settings["notch_auto_hide_seconds"]:
            self._geometry_state = NotchGeometryState.PEEK
            self._idle_since = None

    def reload_settings(self) -> None:
        """Apply validated cached preferences and restart the full idle interval."""
        self._settings = config.get_notch_settings()
        self._reset_deadline()


def __getattr__(name: str) -> Any:
    """Make the Qt adapter available without importing Qt for pure consumers."""
    if name == "NotchWindow":
        from gui.notch_qt import NotchWindow
        globals()[name] = NotchWindow
        return NotchWindow
    raise AttributeError(f"El módulo no expone {name!r}.")
