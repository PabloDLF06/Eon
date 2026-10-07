"""Test lazy CPU voice lifecycle, input validation, atomic WAV output and settings."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave

import numpy as np
import pytest

import config
from core import voice_engine as module
from core.voice_engine import VoiceEngine


def stt_model() -> Mock:
    model = Mock()
    model.model.device = "cpu"
    model.transcribe.return_value = (iter([SimpleNamespace(text=" Hola Pablo. "), SimpleNamespace(text=" Soy Eon. ")]), SimpleNamespace())
    return model


def tts_voice() -> Mock:
    voice = Mock()
    voice.session.get_providers.return_value = ["CPUExecutionProvider"]
    voice.config.num_speakers = 2

    def write_wav(text: str, output: wave.Wave_write, syn_config: module.SynthesisConfig | None = None) -> None:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(bytes(2205 * 2))

    voice.synthesize_wav.side_effect = write_wav
    return voice


def test_voice_constructor_is_lazy() -> None:
    with patch.object(module, "WhisperModel") as stt, patch.object(module.PiperVoice, "load") as tts:
        engine = VoiceEngine()
    assert not engine.is_stt_loaded() and not engine.is_tts_loaded()
    stt.assert_not_called()
    tts.assert_not_called()


def test_stt_loads_once_with_cpu_int8_and_unloads() -> None:
    engine = VoiceEngine()
    with patch.object(module, "WhisperModel", return_value=stt_model()) as factory:
        assert engine.load_stt() and engine.load_stt() and engine.is_stt_loaded()
    factory.assert_called_once()
    assert factory.call_args.kwargs["device"] == "cpu"
    assert factory.call_args.kwargs["compute_type"] == "int8"
    assert engine.unload_stt() and engine.unload_stt() and not engine.is_stt_loaded()


def test_stt_loading_network_or_backend_failure_is_controlled() -> None:
    engine = VoiceEngine()
    with patch.object(module, "WhisperModel", side_effect=RuntimeError("Sin modelo local ni conexión")):
        assert not engine.load_stt()
        assert engine.transcribe(np.zeros(160, dtype=np.int16)) == ""
    assert not engine.is_stt_loaded() and engine.last_error


def test_stt_rejects_non_cpu_backend() -> None:
    model = stt_model()
    model.model.device = "cuda"
    with patch.object(module, "WhisperModel", return_value=model):
        assert not VoiceEngine().load_stt()


def test_transcribe_int16_normalizes_and_forces_spanish() -> None:
    model = stt_model()
    with patch.object(module, "WhisperModel", return_value=model):
        assert VoiceEngine().transcribe(np.full(160, 16384, dtype=np.int16)) == "Hola Pablo. Soy Eon."
    audio = model.transcribe.call_args.args[0]
    assert audio.dtype == np.float32 and np.all(audio == 0.5)
    assert model.transcribe.call_args.kwargs["language"] == "es"


def test_transcribe_accepts_wav_path(tmp_path: Path) -> None:
    path = tmp_path / "input.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(np.array([-32768, 0, 16384], dtype="<i2").tobytes())
    model = stt_model()
    with patch.object(module, "WhisperModel", return_value=model):
        assert VoiceEngine().transcribe(path) == "Hola Pablo. Soy Eon."
    audio = model.transcribe.call_args.args[0]
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    np.testing.assert_array_equal(audio, [-1.0, 0.0, 0.5])


def test_transcribe_stereo_piper_rate_uses_array_and_never_pyav(tmp_path: Path) -> None:
    path = tmp_path / "piper_stereo.wav"
    stereo = np.tile(np.array([16384, 0], dtype="<i2"), (22050, 1))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(stereo.tobytes())
    model = stt_model()
    with patch.object(module, "WhisperModel", return_value=model), patch("av.open", side_effect=AssertionError("PyAV must never decode")) as decoder:
        assert VoiceEngine().transcribe(str(path)) == "Hola Pablo. Soy Eon."
    decoder.assert_not_called()
    audio = model.transcribe.call_args.args[0]
    assert audio.shape == (16000,) and audio.dtype == np.float32
    np.testing.assert_allclose(audio, 0.25)


@pytest.mark.parametrize("sample_rate", [8000, 16000, 22050, 32000, 48000])
def test_transcribe_record_tuple_and_pcm_buffer(sample_rate: int) -> None:
    model = stt_model()
    with patch.object(module, "WhisperModel", return_value=model):
        assert VoiceEngine().transcribe((sample_rate, np.full(sample_rate, 16384, dtype=np.int16)))
        audio = model.transcribe.call_args.args[0]
        assert audio.shape == (16000,) and audio.dtype == np.float32
        np.testing.assert_array_equal(audio, np.full(16000, 0.5, dtype=np.float32))
        pcm = np.array([-32768, 16384], dtype="<i2").tobytes()
        for buffer in (pcm, bytearray(pcm), memoryview(pcm)):
            model.transcribe.return_value = stt_model().transcribe.return_value
            assert VoiceEngine().transcribe(buffer)
            np.testing.assert_array_equal(model.transcribe.call_args.args[0], [-1.0, 0.5])
            model.transcribe.return_value = stt_model().transcribe.return_value
            assert VoiceEngine().transcribe((16000, buffer))
            np.testing.assert_array_equal(model.transcribe.call_args.args[0], [-1.0, 0.5])


def test_transcribe_channel_array_averages_normalized_samples() -> None:
    model = stt_model()
    with patch.object(module, "WhisperModel", return_value=model):
        assert VoiceEngine().transcribe(np.array([[0.5, -0.5], [0.25, 0.75]], dtype=np.float32))
    np.testing.assert_array_equal(model.transcribe.call_args.args[0], [0.0, 0.5])


@pytest.mark.parametrize("width,pcm", [(1, b"\x00\x80\xc0"), (2, b"\x00\x80\x00\x00\x00\x40"),
                                     (3, b"\x00\x00\x80\x00\x00\x00\x00\x00\x40"),
                                     (4, b"\x00\x00\x00\x80\x00\x00\x00\x00\x00\x00\x00\x40")])
def test_pcm_wav_sample_width_normalization(width: int, pcm: bytes, tmp_path: Path) -> None:
    path = tmp_path / "pcm.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(width)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm)
    rate, audio = module._read_pcm_wav(path)
    assert rate == 16000 and audio.dtype == np.float32
    np.testing.assert_array_equal(audio, [-1.0, 0.0, 0.5])


def test_linear_resampler_interpolates_and_keeps_identity() -> None:
    audio = np.array([-1.0, 0.0, 1.0], dtype=np.float32)
    np.testing.assert_array_equal(module._resample_linear(audio, 8000), [-1.0, -0.5, 0.0, 0.5, 1.0, 1.0])
    np.testing.assert_array_equal(module._resample_linear(audio, 48000), [-1.0])
    np.testing.assert_array_equal(module._resample_linear(audio, 16000), audio)
    assert module._resample_linear(np.array([0.5], dtype=np.float32), 48000).tolist() == [0.5]


@pytest.mark.parametrize("rate", [0, -1, 16000.0, True])
def test_transcribe_invalid_record_rate_is_controlled(rate: object) -> None:
    with patch.object(module, "WhisperModel") as factory:
        engine = VoiceEngine()
        assert engine.transcribe((rate, np.zeros(160, dtype=np.int16))) == ""
        assert engine.last_error
    factory.assert_not_called()


@pytest.mark.parametrize("kind", ["invalid", "compressed", "empty", "truncated", "mp3", "ogg"])
def test_transcribe_rejects_unsupported_or_broken_wav(kind: str, tmp_path: Path) -> None:
    path = tmp_path / ("input." + (kind if kind in ("mp3", "ogg") else "wav"))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * (0 if kind == "empty" else 100))
    data = path.read_bytes()
    if kind == "compressed":
        data = data[:20] + b"\x03\x00" + data[22:]
    elif kind == "invalid":
        data = b"not WAV"
    elif kind == "truncated":
        data = data[:-2]
    path.write_bytes(data)
    with patch.object(module, "WhisperModel") as factory, patch("av.open", side_effect=AssertionError("No PyAV decoding")) as decoder:
        engine = VoiceEngine()
        assert engine.transcribe(path) == "" and engine.last_error
    factory.assert_not_called()
    decoder.assert_not_called()


def test_transcribe_consumes_lazy_segments_inside_exception_boundary() -> None:
    def broken_segments():
        yield SimpleNamespace(text="Parcial")
        raise RuntimeError("Decodificador fallido")

    model = stt_model()
    model.transcribe.return_value = (broken_segments(), None)
    with patch.object(module, "WhisperModel", return_value=model):
        assert VoiceEngine().transcribe(np.zeros(160, dtype=np.float32)) == ""


@pytest.mark.parametrize("audio", [np.array([], dtype=np.int16), np.zeros((2, 2, 2)), np.array([2.0]), np.array([float("nan")]), np.array([1], dtype=np.int32), "absent.wav"])
def test_invalid_audio_returns_empty_without_loading(audio: object) -> None:
    with patch.object(module, "WhisperModel") as factory:
        assert VoiceEngine().transcribe(audio) == ""
    factory.assert_not_called()


def test_tts_loads_once_on_cpu_and_unloads() -> None:
    voice = tts_voice()
    engine = VoiceEngine()
    with patch.object(module.PiperVoice, "load", return_value=voice) as factory:
        assert engine.load_tts() and engine.load_tts() and engine.is_tts_loaded()
    factory.assert_called_once()
    assert factory.call_args.kwargs["use_cuda"] is False
    assert factory.call_args.args[0].name == "es_ES-sharvard-medium.onnx"
    assert engine.unload_tts() and engine.unload_tts() and not engine.is_tts_loaded()


def test_tts_missing_asset_is_controlled() -> None:
    with patch.object(module.PiperVoice, "load", side_effect=FileNotFoundError("Sin voz")):
        engine = VoiceEngine()
        assert not engine.load_tts() and engine.synthesize("Hola") == ""


def test_tts_rejects_non_cpu_session() -> None:
    voice = tts_voice()
    voice.session.get_providers.return_value = ["CUDAExecutionProvider"]
    with patch.object(module.PiperVoice, "load", return_value=voice):
        assert not VoiceEngine().load_tts()


def test_synthesize_generates_valid_wav_from_replaced_piper(tmp_path: Path) -> None:
    path = tmp_path / "speech.wav"
    with patch.object(module.PiperVoice, "load", return_value=tts_voice()):
        assert VoiceEngine().synthesize("Hola Pablo", str(path)) == str(path)
    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        assert audio.getframerate() == 22050 and audio.getnframes() == 2205
    assert list(tmp_path.glob("*.wav")) == [path]


def test_default_synthesis_path_is_runtime_wav(tmp_path: Path) -> None:
    with patch.object(module, "ROOT", tmp_path), patch.object(module.PiperVoice, "load", return_value=tts_voice()):
        assert VoiceEngine().synthesize("Hola") == str(tmp_path / ".runtime/tts_output.wav")


def test_failed_synthesis_preserves_existing_output_and_removes_temporary(tmp_path: Path) -> None:
    destination = tmp_path / "original.wav"
    destination.write_bytes(b"previous user file")
    voice = tts_voice()
    voice.synthesize_wav.side_effect = RuntimeError("Síntesis fallida")
    with patch.object(module.PiperVoice, "load", return_value=voice):
        assert VoiceEngine().synthesize("Hola", str(destination)) == ""
    assert destination.read_bytes() == b"previous user file"
    assert list(tmp_path.iterdir()) == [destination]


def test_empty_synthesis_is_rejected_without_loading() -> None:
    with patch.object(module.PiperVoice, "load") as factory:
        assert VoiceEngine().synthesize(" ") == ""
    factory.assert_not_called()


def test_synthesis_rejects_non_wav_path(tmp_path: Path) -> None:
    with patch.object(module.PiperVoice, "load") as factory:
        assert VoiceEngine().synthesize("Hola", str(tmp_path / "file.mp3")) == ""
    factory.assert_not_called()


def test_voice_lifecycle_logging_contains_timestamp_and_operations(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    caplog.set_level("INFO", logger=module.__name__)
    with patch.object(module, "WhisperModel", return_value=stt_model()), patch.object(module.PiperVoice, "load", return_value=tts_voice()):
        engine = VoiceEngine()
        engine.transcribe(np.zeros(160, dtype=np.int16))
        engine.synthesize("Hola", str(tmp_path / "sample.wav"))
        engine.unload_stt()
        engine.unload_tts()
    for event in ("carga_stt", "descarga_stt", "transcripcion", "carga_tts", "descarga_tts", "sintesis"):
        assert f"evento={event}" in caplog.text
    assert "timestamp=" in caplog.text and "dispositivo=cpu" in caplog.text


@pytest.mark.parametrize("field,value", [
    ("stt_model", "inventado"), ("stt_model", 1), ("stt_device", "cuda"),
    ("stt_compute_type", "float16"), ("tts_engine", "cloud"),
    ("vad_aggressiveness", -1), ("vad_aggressiveness", 4), ("vad_aggressiveness", True),
    ("sample_rate", 44100), ("sample_rate", 16000.0), ("wake_word_model", "hey_eon"),
])
def test_config_rejects_invalid_voice_field(field: str, value: object, tmp_path: Path) -> None:
    settings = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    settings["voice_settings"][field] = value
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match=field):
        config._load_settings(path)


@pytest.mark.parametrize("field", ["stt_model", "stt_device", "stt_compute_type", "tts_engine", "vad_aggressiveness", "sample_rate", "wake_word_model",
                                  "tts_speaker_id", "tts_length_scale", "tts_noise_scale", "tts_noise_w_scale", "tts_pronunciation_aliases"])
def test_config_requires_each_voice_field(field: str, tmp_path: Path) -> None:
    settings = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    del settings["voice_settings"][field]
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match="voice_settings"):
        config._load_settings(path)


def test_config_voice_settings_requires_object_and_rejects_unknown_fields(tmp_path: Path) -> None:
    settings = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    for value in (None, [], {**settings["voice_settings"], "cloud_key": "not-a-secret"}):
        settings["voice_settings"] = value
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(settings), encoding="utf-8")
        with pytest.raises(config.ConfigurationError, match="voice_settings"):
            config._load_settings(path)


def test_voice_settings_accessor_returns_copy() -> None:
    copy = config.get_voice_settings()
    copy["stt_device"] = "cuda"
    assert config.get_voice_settings()["stt_device"] == "cpu"
    copy["tts_pronunciation_aliases"]["Eon"] = "Different"
    assert config.get_voice_settings()["tts_pronunciation_aliases"]["Eon"] == "Eeeón"


@pytest.mark.parametrize("text,expected", [
    ("Hola, soy Eon", "Hola, soy Eeeón"),
    ("EON y Eon.", "Eeeón y Eeeón."),
    ("(Eon), ¡EON! ¿Eon?", "(Eeeón), ¡Eeeón! ¿Eeeón?"),
    ("preEon Eonario EONX Eon_1 Eon2 áEon Eoná eon", "preEon Eonario EONX Eon_1 Eon2 áEon Eoná eon"),
])
def test_synthesize_aliases_only_piper_input_and_keeps_original_text_and_logs(
    text: str, expected: str, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    voice = tts_voice()
    output_path = tmp_path / "Eon.wav"
    original = text
    with patch.object(module.PiperVoice, "load", return_value=voice):
        result = VoiceEngine().synthesize(text, str(output_path))
    assert result == str(output_path) and output_path.name == "Eon.wav"
    assert text == original
    assert voice.synthesize_wav.call_args.args[0] == expected
    assert "Eeeón" not in caplog.text and "evento=sintesis" in caplog.text
    synthesis = voice.synthesize_wav.call_args.kwargs["syn_config"]
    assert isinstance(synthesis, module.SynthesisConfig)
    assert (synthesis.speaker_id, synthesis.length_scale, synthesis.noise_scale, synthesis.noise_w_scale) == (0, 1.15, 0.7337, 0.88)
    assert synthesis.normalize_audio is True and synthesis.volume == 1.0


def test_aliases_are_literal_nonrecursive_and_can_be_disabled() -> None:
    assert module._apply_pronunciation_aliases("Eon Eeeón", {"Eon": "Eeeón", "Eeeón": "Other"}) == "Eeeón Other"
    assert module._apply_pronunciation_aliases("E.on Eon", {"E.on": "Name"}) == "Name Eon"
    assert module._apply_pronunciation_aliases("Eon", {}) == "Eon"


def test_tts_speaker_not_in_loaded_voice_is_controlled() -> None:
    voice = tts_voice()
    engine = VoiceEngine()
    engine._settings["tts_speaker_id"] = 2
    with patch.object(module.PiperVoice, "load", return_value=voice):
        assert not engine.load_tts() and not engine.is_tts_loaded()
    assert engine.last_error


@pytest.mark.parametrize("field,value", [
    ("tts_speaker_id", -1), ("tts_speaker_id", True), ("tts_speaker_id", 0.0), ("tts_speaker_id", "0"),
    ("tts_length_scale", 0), ("tts_length_scale", 0.49), ("tts_length_scale", 2.01),
    ("tts_length_scale", float("nan")), ("tts_length_scale", float("inf")), ("tts_length_scale", True), ("tts_length_scale", "1.15"),
    ("tts_noise_scale", -0.01), ("tts_noise_scale", 2.01), ("tts_noise_scale", float("nan")),
    ("tts_noise_scale", float("inf")), ("tts_noise_scale", True), ("tts_noise_scale", "0.7337"),
    ("tts_noise_w_scale", -0.01), ("tts_noise_w_scale", 2.01), ("tts_noise_w_scale", float("nan")),
    ("tts_noise_w_scale", float("inf")), ("tts_noise_w_scale", True), ("tts_noise_w_scale", "0.88"),
    ("tts_pronunciation_aliases", []), ("tts_pronunciation_aliases", None),
    ("tts_pronunciation_aliases", {"": "Name"}), ("tts_pronunciation_aliases", {"Eon": ""}),
    ("tts_pronunciation_aliases", {" Eon": "Name"}), ("tts_pronunciation_aliases", {"Eon ": "Name"}),
    ("tts_pronunciation_aliases", {"Eon": " Name"}), ("tts_pronunciation_aliases", {"Eon": "Name "}),
    ("tts_pronunciation_aliases", {"Eon": 1}), ("tts_pronunciation_aliases", {"Eon": True}),
])
def test_config_rejects_invalid_tts_setting(field: str, value: object, tmp_path: Path) -> None:
    settings = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    settings["voice_settings"][field] = value
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    with pytest.raises(config.ConfigurationError, match=field):
        config._load_settings(path)


@pytest.mark.parametrize("field,value", [
    ("tts_speaker_id", 0), ("tts_speaker_id", 1),
    ("tts_length_scale", 0.5), ("tts_length_scale", 1), ("tts_length_scale", 2.0),
    ("tts_noise_scale", 0), ("tts_noise_scale", 2),
    ("tts_noise_w_scale", 0.0), ("tts_noise_w_scale", 2.0),
    ("tts_pronunciation_aliases", {}), ("tts_pronunciation_aliases", {"Eon": "Eeeón", "EON": "Eeeón"}),
])
def test_config_accepts_valid_tts_settings_and_bounds(field: str, value: object, tmp_path: Path) -> None:
    settings = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    settings["voice_settings"][field] = value
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    assert config._load_settings(path)["voice_settings"][field] == value


def test_visible_project_name_and_selected_voice_are_not_renamed() -> None:
    settings = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert settings["voice_profile"] == "es_ES-sharvard-medium"
    assert config.get_voice_profile() == "es_ES-sharvard-medium"
    assert (module.ROOT / "docs/STATUS.md").read_text(encoding="utf-8").startswith("# EON — Estado real")
    assert (module.ROOT / "docs/DECISIONS.md").read_text(encoding="utf-8").startswith("# EON — Decisiones")
