"""Verify perceptual gain and peak timing without Qt or audio hardware."""

import math
import pytest

from gui.microphone_meter import MicrophoneMeter, rms_to_level


@pytest.mark.parametrize("rms,expected", [(0, 0), (1e-6, 0), (0.001, 0),
    (10 ** (-50 / 20), 0), (0.01, 10 / 42), (0.1, 30 / 42),
    (10 ** (-8 / 20), 1), (1, 1)])
def test_rms_perceptual_scale(rms: float, expected: float) -> None:
    assert rms_to_level(rms) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("rms", [-0.1, 1.1, math.nan, math.inf, True, None, "0.1"])
def test_invalid_level_is_not_silence(rms: object) -> None:
    with pytest.raises(ValueError):
        rms_to_level(rms)


def test_peak_holds_then_falls_independently_of_sample_rate() -> None:
    now = [0.0]
    meter = MicrophoneMeter(clock=lambda: now[0])
    meter.update(0.1)
    peak = meter.peak
    meter.update(0)
    now[0] = 0.299
    assert meter.tick() == peak
    now[0] = 0.4
    assert meter.tick() == pytest.approx(peak - 0.065)
    now[0] = 3
    assert meter.tick() == 0
    meter.update(0.4)
    assert meter.peak == 1
    meter.reset()
    assert meter.level == meter.peak == 0


def test_peak_never_falls_below_current_signal() -> None:
    now = [0.0]
    meter = MicrophoneMeter(clock=lambda: now[0])
    meter.update(0.4)
    meter.update(0.01)
    now[0] = 5
    assert meter.tick() == meter.level == pytest.approx(10 / 42)
