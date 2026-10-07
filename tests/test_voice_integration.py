"""Run the explicitly enabled real CPU voice and microphone round-trip once."""

from __future__ import annotations

import gc
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import time
import wave

import pytest

import config
from core.acoustic_detector import AcousticDetector, WakeWordDetector
from core.model_router import ModelRouter
from core.voice_engine import VoiceEngine

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.getenv("EON_RUN_VOICE_INTEGRATION") != "1", reason="Integración real de voz: requiere EON_RUN_VOICE_INTEGRATION=1 y micrófono local.")
def test_real_cpu_voice_round_trip_and_microphone() -> None:
    """Exercise real microphone/VAD, CPU STT/TTS and a two-second wake listen."""
    started = time.perf_counter()
    engine = VoiceEngine()
    wake: WakeWordDetector | None = None
    evidence = {"versions": {package: version(package) for package in (
        "sounddevice", "faster-whisper", "openwakeword", "webrtcvad-wheels", "piper-tts", "numpy", "ctranslate2", "onnxruntime", "av",
    )}}
    telemetry = ModelRouter()
    evidence["vram_before"] = telemetry.measure_real_vram_snapshot()
    evidence["voice_profile"] = config.get_voice_profile()
    evidence["tts_settings"] = {key: value for key, value in config.get_voice_settings().items() if key.startswith("tts_")}
    try:
        assert len(subprocess.run(["ollama", "ps"], check=True, capture_output=True, text=True, timeout=10).stdout.strip().splitlines()) == 1
        acoustic = AcousticDetector()
        evidence["microphones"] = [device for device in acoustic.list_devices() if device["max_input_channels"] > 0]
        assert evidence["microphones"], acoustic.last_error or "No hay micrófono real."
        sample_rate, audio = acoustic.record(3.0)
        assert audio.size == sample_rate * 3 and acoustic.last_error is None, acoustic.last_error
        frames = [audio[offset:offset + 480].tobytes() for offset in range(0, audio.size, 480)]
        evidence["recording"] = {"sample_rate": sample_rate, "samples": int(audio.size), "seconds": 3.0,
                                 "rms": acoustic.pcm_rms(audio.tobytes()),
                                 "vad_speech_frames": sum(acoustic.is_speech(frame, sample_rate) for frame in frames)}
        assert engine.load_stt(), engine.last_error
        evidence["stt_device"] = engine._stt.model.device
        assert engine.load_tts(), engine.last_error
        assert evidence["voice_profile"] == "es_ES-sharvard-medium"
        assert evidence["tts_settings"] == {
            "tts_engine": "piper", "tts_speaker_id": 0, "tts_length_scale": 1.15,
            "tts_noise_scale": 0.7337, "tts_noise_w_scale": 0.88,
            "tts_pronunciation_aliases": {"Eon": "Eeeón", "EON": "Eeeón"},
        }
        assert Path(engine._tts.session._model_path).name == "es_ES-sharvard-medium.onnx"
        evidence["piper_providers"] = engine._tts.session.get_providers()
        sample_path = ROOT / ".runtime/fase3_tts_sample.wav"
        text = "Hola Pablo, soy Eon, y mi voz ya funciona en local."
        output = engine.synthesize(text, str(sample_path))
        assert output and sample_path.is_file(), engine.last_error
        with wave.open(str(sample_path), "rb") as wav_file:
            evidence["tts_sample"] = {"path": str(sample_path), "bytes": sample_path.stat().st_size,
                                      "seconds": wav_file.getnframes() / wav_file.getframerate(),
                                      "sample_rate": wav_file.getframerate(), "channels": wav_file.getnchannels()}
        evidence["tts_text"] = text
        evidence["round_trip_text"] = engine.transcribe(str(sample_path))
        assert evidence["round_trip_text"] and engine.last_error is None, engine.last_error
        wake = WakeWordDetector(config.get_voice_settings()["wake_word_model"])
        assert wake.is_available(), wake.last_error
        evidence["wake_word"] = wake.model_name
        evidence["wake_providers"] = [session.get_providers() for session in (
            *wake._model.models.values(), wake._model.preprocessor.melspec_model, wake._model.preprocessor.embedding_model,
        )]
        evidence["wake_silence_detected"] = wake.listen_once(2.0)
        assert not evidence["wake_silence_detected"] and wake.last_error is None, wake.last_error
        evidence["status"] = "operations_completed"
    finally:
        engine.unload_stt()
        engine.unload_tts()
        wake = None
        gc.collect()
        evidence["vram_after"] = telemetry.measure_real_vram_snapshot()
        evidence["ollama_ps_after"] = subprocess.run(["ollama", "ps"], check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        evidence["elapsed_seconds"] = time.perf_counter() - started
        evidence["status"] = "passed" if (
            evidence.get("status") == "operations_completed"
            and evidence["vram_before"] and evidence["vram_before"] == evidence["vram_after"]
            and len(evidence["ollama_ps_after"].splitlines()) == 1
        ) else "failed"
        (ROOT / ".runtime/fase3_voice_integration_sharvard.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("INTEGRACIÓN REAL DE VOZ:\n" + json.dumps(evidence, ensure_ascii=False, indent=2), flush=True)
    assert evidence["vram_before"] and evidence["vram_after"]
    assert evidence["vram_before"] == evidence["vram_after"], "La VRAM basal cambió; revisar procesos externos y providers."
    assert len(evidence["ollama_ps_after"].splitlines()) == 1
