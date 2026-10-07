"""Test sustained interruption detection, thread lifecycle and capture failures."""

from __future__ import annotations

import threading
from unittest.mock import Mock, patch

import numpy as np
import pytest

from core.acoustic_detector import AcousticDetector
from core.barge_in import BargeInDetector


def acoustic_source(values: list[int], speech: list[bool]) -> Mock:
    acoustic = Mock(spec=AcousticDetector)
    acoustic.sample_rate, acoustic.last_error = 16000, None
    acoustic.pcm_rms.side_effect = AcousticDetector.pcm_rms
    acoustic.is_speech.side_effect = speech
    acoustic.iter_frames.return_value = (np.full(480, value, dtype=np.int16).tobytes() for value in values)
    return acoustic


def test_sustained_voice_interrupts_on_worker() -> None:
    detector = BargeInDetector(acoustic_source([2000] * 3, [True] * 3))
    detector.start_monitoring()
    detector._thread.join(timeout=1)
    assert detector.is_interrupted()
    detector.stop_monitoring()
    assert detector._thread is None


def test_silence_does_not_interrupt() -> None:
    detector = BargeInDetector(acoustic_source([0] * 4, [False] * 4))
    detector.start_monitoring()
    detector._thread.join(timeout=1)
    assert not detector.is_interrupted()
    detector.stop_monitoring()


def test_energy_and_vad_are_both_required() -> None:
    for values, speech in [([2000] * 3, [False] * 3), ([10] * 3, [True] * 3)]:
        detector = BargeInDetector(acoustic_source(values, speech))
        detector.start_monitoring()
        detector._thread.join(timeout=1)
        assert not detector.is_interrupted()
        detector.stop_monitoring()


def test_nonconsecutive_speech_does_not_interrupt() -> None:
    detector = BargeInDetector(acoustic_source([2000] * 5, [True, True, False, True, True]))
    detector.start_monitoring()
    detector._thread.join(timeout=1)
    assert not detector.is_interrupted()
    detector.stop_monitoring()


def test_monitoring_starts_once_and_stops_cleanly_without_blocking_start() -> None:
    acoustic = acoustic_source([], [])
    started = threading.Event()

    def frames(**kwargs: object):
        started.set()
        kwargs["stop_event"].wait(1)
        yield bytes(960)

    acoustic.iter_frames.side_effect = frames
    detector = BargeInDetector(acoustic)
    detector.start_monitoring()
    assert started.wait(1)
    first_thread = detector._thread
    detector.start_monitoring()
    assert detector._thread is first_thread
    detector.stop_monitoring()
    assert not first_thread.is_alive() and detector._thread is None
    detector.stop_monitoring()


def test_capture_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    acoustic = acoustic_source([], [])
    acoustic.iter_frames.side_effect = RuntimeError("Micrófono desconectado")
    detector = BargeInDetector(acoustic)
    detector.start_monitoring()
    detector._thread.join(timeout=1)
    detector.stop_monitoring()
    assert not detector.is_interrupted() and detector.last_error == "Micrófono desconectado"
    assert "Fallo controlado" in caplog.text


def test_new_run_resets_interruption_latch() -> None:
    acoustic = acoustic_source([2000] * 3, [True] * 3)
    detector = BargeInDetector(acoustic)
    detector.start_monitoring()
    detector._thread.join(timeout=1)
    detector.stop_monitoring()
    assert detector.is_interrupted()
    acoustic.iter_frames.return_value = (frame for frame in [bytes(960)])
    acoustic.is_speech.side_effect = [False]
    detector.start_monitoring()
    detector._thread.join(timeout=1)
    detector.stop_monitoring()
    assert not detector.is_interrupted()


def test_thread_start_failure_is_controlled() -> None:
    detector = BargeInDetector(acoustic_source([], []))
    with patch.object(threading.Thread, "start", side_effect=RuntimeError("Sin hilo")):
        detector.start_monitoring()
    assert detector.last_error == "Sin hilo" and not detector.is_interrupted()


@pytest.mark.parametrize("arguments", [{"energy_threshold": 0}, {"energy_threshold": 2}, {"energy_threshold": True}, {"energy_threshold": float("nan")}, {"consecutive_frames": 0}, {"consecutive_frames": True}])
def test_barge_constructor_rejects_invalid_parameters(arguments: dict) -> None:
    with pytest.raises(ValueError):
        BargeInDetector(acoustic_source([], []), **arguments)
