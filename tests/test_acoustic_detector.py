"""Test microphone failures, PCM framing, VAD and CPU wake detection without hardware."""

from __future__ import annotations

import logging
from pathlib import Path
import threading
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest

from core.acoustic_detector import AcousticDetector, WakeWordDetector
from core import acoustic_detector as module


def input_stream(frame_count: int = 2, value: int = 0, *, bad_length: bool = False) -> Mock:
    """Build a context manager that supplies PCM through the real callback."""
    def create(**kwargs: object) -> MagicMock:
        stream = MagicMock()

        def enter() -> MagicMock:
            length = int(kwargs["blocksize"]) - int(bad_length)
            for _ in range(frame_count):
                kwargs["callback"](np.full((length, 1), value, dtype=np.int16), length, None, None)
            return stream

        stream.__enter__.side_effect = enter
        return stream

    return Mock(side_effect=create)


def test_devices_are_real_query_results() -> None:
    detector = AcousticDetector()
    with patch.object(module.sd, "query_devices", return_value=[{"name": "Mic", "max_input_channels": 1}]):
        assert detector.list_devices() == [{"index": 0, "name": "Mic", "max_input_channels": 1}]


def test_no_audio_devices_is_controlled() -> None:
    with patch.object(module.sd, "query_devices", return_value=[]):
        assert AcousticDetector().list_devices() == []


def test_device_query_exception_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    detector = AcousticDetector()
    with patch.object(module.sd, "query_devices", side_effect=RuntimeError("Sin PortAudio")):
        assert detector.list_devices() == []
    assert detector.last_error == "Sin PortAudio" and "enumerar" in caplog.text


def test_record_returns_exact_mono_int16_samples() -> None:
    detector = AcousticDetector()
    with patch.object(module.sd, "InputStream", input_stream(value=2048)) as stream:
        rate, audio = detector.record(0.05)
    assert rate == 16000 and audio.shape == (800,) and audio.dtype == np.int16
    assert np.all(audio == 2048)
    assert stream.call_args.kwargs["channels"] == 1
    assert stream.call_args.kwargs["dtype"] == "int16"


def test_record_hardware_error_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    detector = AcousticDetector()
    with patch.object(module.sd, "InputStream", side_effect=RuntimeError("Micrófono ocupado")):
        rate, audio = detector.record(0.03)
        assert detector.get_input_level(0.03) == 0.0
    assert rate == 16000 and audio.size == 0 and detector.last_error
    assert "Fallo controlado" in caplog.text


@pytest.mark.parametrize("duration", [0, -1, True, float("nan"), float("inf"), "3"])
def test_record_rejects_invalid_duration(duration: object) -> None:
    with pytest.raises(ValueError):
        AcousticDetector().record(duration)


@pytest.mark.parametrize("rate", [8000, 16000, 32000, 48000])
@pytest.mark.parametrize("milliseconds", [10, 20, 30])
def test_vad_accepts_all_supported_frame_sizes(rate: int, milliseconds: int) -> None:
    detector = AcousticDetector()
    frame = bytes(rate * milliseconds // 1000 * 2)
    with patch.object(detector._vad, "is_speech", return_value=True) as vad:
        assert detector.is_speech(frame, rate)
        vad.assert_called_once_with(frame, rate)


def test_vad_silence_and_simulated_voice() -> None:
    detector = AcousticDetector()
    silence = bytes(960)
    voice = np.full(480, 3000, dtype=np.int16).tobytes()
    with patch.object(detector._vad, "is_speech", side_effect=[False, True]):
        assert not detector.is_speech(silence, 16000)
        assert detector.is_speech(voice, 16000)


@pytest.mark.parametrize("frame,rate", [(b"odd", 16000), (b"", 16000), (bytes(960), 44100), (bytes(960), True)])
def test_invalid_vad_input_fails_closed(frame: bytes, rate: int) -> None:
    assert not AcousticDetector().is_speech(frame, rate)


def test_vad_exception_is_controlled() -> None:
    detector = AcousticDetector()
    with patch.object(detector._vad, "is_speech", side_effect=RuntimeError("VAD roto")):
        assert not detector.is_speech(bytes(960), 16000)
    assert detector.last_error == "VAD roto"


def test_normalized_rms_avoids_integer_overflow() -> None:
    detector = AcousticDetector()
    audio = np.full(100, -32768, dtype=np.int16)
    with patch.object(detector, "record", return_value=(16000, audio)):
        assert detector.get_input_level(0.1) == 1.0
    assert detector.pcm_rms(bytes(960)) == 0.0


def test_capture_rejects_concurrent_microphone_owner() -> None:
    detector = AcousticDetector()
    module._MICROPHONE_LOCK.acquire()
    try:
        assert list(detector.iter_frames(timeout_seconds=0.05)) == []
        assert "ocupado" in detector.last_error
    finally:
        module._MICROPHONE_LOCK.release()


def test_capture_can_stop_and_release_microphone() -> None:
    stop = threading.Event()
    stop.set()
    with patch.object(module.sd, "InputStream", input_stream(frame_count=0)):
        assert list(AcousticDetector().iter_frames(stop_event=stop)) == []
    assert module._MICROPHONE_LOCK.acquire(blocking=False)
    module._MICROPHONE_LOCK.release()


def test_capture_bad_frame_size_is_controlled() -> None:
    detector = AcousticDetector()
    with patch.object(module.sd, "InputStream", input_stream(bad_length=True)):
        _, audio = detector.record(0.03)
    assert audio.size == 0 and "tamaño inesperado" in detector.last_error


def test_capture_queue_overflow_does_not_deliver_silently_corrupt_audio() -> None:
    detector = AcousticDetector()
    with patch.object(module.sd, "InputStream", input_stream(frame_count=51)):
        _, audio = detector.record(0.03)
    assert not audio.size and "cola" in detector.last_error


@pytest.mark.parametrize("arguments", [{"sample_rate": 44100}, {"sample_rate": True}, {"vad_aggressiveness": 4}, {"vad_aggressiveness": True}])
def test_acoustic_constructor_validates_configuration(arguments: dict) -> None:
    with pytest.raises(ValueError):
        AcousticDetector(**arguments)


def prepare_wake_model(tmp_path: Path, scores: list[float]) -> tuple[Mock, Mock]:
    for filename in ("hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx"):
        (tmp_path / filename).write_bytes(b"test-only model file")
    session = Mock()
    session.get_providers.return_value = ["CPUExecutionProvider"]
    model = Mock()
    model.models = {"hey_jarvis_v0.1": session}
    model.preprocessor.melspec_model = session
    model.preprocessor.embedding_model = session
    model.predict.side_effect = [{"hey_jarvis_v0.1": score} for score in scores]
    return model, session


def test_wake_model_uses_only_cpu_and_detects(tmp_path: Path) -> None:
    import openwakeword

    model, _ = prepare_wake_model(tmp_path, [0.1, 0.8])
    acoustic = Mock(spec=AcousticDetector)
    acoustic.sample_rate, acoustic.last_error = 16000, None
    acoustic.iter_frames.return_value = (frame for frame in [bytes(2560), bytes(2560)])
    with patch.object(openwakeword, "Model", return_value=model) as factory:
        detector = WakeWordDetector("hey_jarvis", acoustic_detector=acoustic, model_directory=tmp_path)
    assert detector.is_available() and detector.listen_once(2.0)
    assert factory.call_args.kwargs["device"] == "cpu"
    assert factory.call_args.kwargs["inference_framework"] == "onnx"
    model.reset.assert_called_once()


def test_wake_silence_returns_false(tmp_path: Path) -> None:
    import openwakeword

    model, _ = prepare_wake_model(tmp_path, [0.0])
    acoustic = Mock(spec=AcousticDetector)
    acoustic.sample_rate, acoustic.last_error = 16000, None
    acoustic.iter_frames.return_value = (frame for frame in [bytes(2560)])
    with patch.object(openwakeword, "Model", return_value=model):
        detector = WakeWordDetector("hey_jarvis", acoustic_detector=acoustic, model_directory=tmp_path)
    assert not detector.listen_once(0.1) and detector.last_error is None


def test_wake_missing_model_is_not_available(tmp_path: Path) -> None:
    detector = WakeWordDetector("hey_jarvis", model_directory=tmp_path)
    assert not detector.is_available() and not detector.listen_once(0.1)


def test_wake_load_failure_is_controlled(tmp_path: Path) -> None:
    import openwakeword

    prepare_wake_model(tmp_path, [])
    with patch.object(openwakeword, "Model", side_effect=RuntimeError("ONNX ilegible")):
        assert not WakeWordDetector("hey_jarvis", model_directory=tmp_path).is_available()


def test_wake_rejects_non_cpu_session(tmp_path: Path) -> None:
    import openwakeword

    model, session = prepare_wake_model(tmp_path, [])
    session.get_providers.return_value = ["CUDAExecutionProvider"]
    with patch.object(openwakeword, "Model", return_value=model):
        assert not WakeWordDetector("hey_jarvis", model_directory=tmp_path).is_available()


def test_wake_prediction_failure_does_not_crash(tmp_path: Path) -> None:
    import openwakeword

    model, _ = prepare_wake_model(tmp_path, [])
    model.predict.side_effect = RuntimeError("Predicción fallida")
    acoustic = Mock(spec=AcousticDetector)
    acoustic.sample_rate, acoustic.last_error = 16000, None
    acoustic.iter_frames.return_value = (frame for frame in [bytes(2560)])
    with patch.object(openwakeword, "Model", return_value=model):
        detector = WakeWordDetector("hey_jarvis", acoustic_detector=acoustic, model_directory=tmp_path)
    assert not detector.listen_once(0.1) and detector.last_error == "Predicción fallida"


def test_wake_catalog_contains_only_installed_builtin_ids() -> None:
    import openwakeword

    assert WakeWordDetector.available_models() == sorted(openwakeword.MODELS)
    with pytest.raises(ValueError):
        WakeWordDetector("hey_eon")


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan")])
def test_wake_rejects_invalid_timeout(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError):
        WakeWordDetector("hey_jarvis", model_directory=tmp_path).listen_once(timeout)
