"""Map normalized RMS to perceptual levels and a clock-driven falling peak."""

from collections.abc import Callable
import math
import time


def rms_to_level(rms: float) -> float:
    """Return 0–1 for −50 to −8 dBFS; reject invalid capture data."""
    if type(rms) not in (int, float) or not math.isfinite(rms) or not 0 <= rms <= 1:
        raise ValueError("El nivel RMS debe ser finito y estar entre 0 y 1.")
    db = 20 * math.log10(max(rms, 1e-6))
    return max(0.0, min(1.0, (db + 50) / 42))


class MicrophoneMeter:
    """Hold peaks for 300 ms, then fall at 0.65 full-scale units per second."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self.level = 0.0
        self.peak = 0.0
        self._held_until = self._clock()
        self._last_tick = self._held_until

    def update(self, rms: float) -> float:
        level = rms_to_level(rms)
        self.tick()
        self.level = level
        if level >= self.peak:
            self.peak = level
            self._held_until = self._clock() + 0.3
        return level

    def tick(self) -> float:
        now = self._clock()
        elapsed = max(0.0, now - max(self._last_tick, self._held_until))
        self.peak = max(self.level, self.peak - 0.65 * elapsed)
        self._last_tick = now
        return self.peak
