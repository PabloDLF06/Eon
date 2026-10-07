"""Run lazy local Whisper STT and Piper WAV synthesis exclusively on CPU."""

from __future__ import annotations

from datetime import datetime, timezone
import gc
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any
import wave

from faster_whisper import WhisperModel
import numpy as np
from piper import PiperVoice
from piper.config import SynthesisConfig

import config

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def _apply_pronunciation_aliases(text: str, aliases: dict[str, str]) -> str:
    """Map whole Unicode words/phrases once, case-sensitively, for Piper input only."""
    if not aliases:
        return text
    alternatives = "|".join(re.escape(source) for source in sorted(aliases, key=len, reverse=True))
    return re.sub(r"(?<!\w)(?:" + alternatives + r")(?!\w)",
                  lambda match: aliases[match.group(0)], text)


def _normalize_audio(samples: Any) -> np.ndarray:
    """Return finite mono float32 from int16 PCM bytes or mono/channel arrays."""
    if isinstance(samples, (bytes, bytearray, memoryview)):
        samples = np.frombuffer(samples, dtype="<i2")
    samples = np.asarray(samples)
    if samples.ndim not in (1, 2) or not samples.size:
        raise ValueError("El audio debe ser un array mono o de muestras por canal, no vacío.")
    if samples.dtype.kind == "i" and samples.dtype.itemsize == 2:
        audio = samples.astype(np.float32) / 32768.0
    elif np.issubdtype(samples.dtype, np.floating):
        audio = samples.astype(np.float32)
    else:
        raise ValueError("El array debe ser int16 o flotante normalizado.")
    if not np.isfinite(audio).all() or np.abs(audio).max() > 1.0:
        raise ValueError("El audio flotante debe estar entre -1 y 1, sin NaN ni infinitos.")
    if audio.ndim == 2:
        audio = audio.mean(axis=1, dtype=np.float32)
    return np.ascontiguousarray(audio, dtype=np.float32)


def _resample_linear(audio: np.ndarray, sample_rate: int, target_rate: int = 16000) -> np.ndarray:
    """Resample mono audio with basic NumPy linear interpolation for STT, not hi-fi.

    The final source sample is held at the boundary. No anti-alias filter is
    applied, so this deliberately simple converter is not a playback resampler.
    """
    if any(type(rate) is not int or rate <= 0 for rate in (sample_rate, target_rate)):
        raise ValueError("Las frecuencias de audio deben ser enteros positivos en Hz.")
    if audio.ndim != 1 or not audio.size:
        raise ValueError("El remuestreo requiere audio mono no vacío.")
    if sample_rate == target_rate:
        return np.ascontiguousarray(audio, dtype=np.float32)
    output_size = max(1, round(audio.size * target_rate / sample_rate))
    source_positions = np.arange(audio.size, dtype=np.float64)
    target_positions = np.arange(output_size, dtype=np.float64) * sample_rate / target_rate
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def _read_pcm_wav(path: Path) -> tuple[int, np.ndarray]:
    """Read uncompressed little-endian WAV PCM 8/16/24/32-bit without PyAV."""
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getcomptype() != "NONE":
            raise ValueError("Solo se admite WAV PCM sin comprimir en Fase 3.")
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
        raw_audio = wav_file.readframes(frame_count)
    if sample_width not in (1, 2, 3, 4):
        raise ValueError("El WAV PCM debe tener muestras de 8, 16, 24 o 32 bits.")
    if not frame_count or len(raw_audio) != frame_count * channels * sample_width:
        raise ValueError("El WAV debe contener muestras PCM completas y no vacías.")
    if sample_width == 1:
        audio = (np.frombuffer(raw_audio, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 3:
        octets = np.frombuffer(raw_audio, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = octets[:, 0] | (octets[:, 1] << 8) | (octets[:, 2] << 16)
        values = (values ^ 0x800000) - 0x800000
        audio = values.astype(np.float32) / 8388608.0
    else:
        audio = np.frombuffer(raw_audio, dtype=f"<i{sample_width}").astype(np.float32) / float(1 << (8 * sample_width - 1))
    return sample_rate, _normalize_audio(audio.reshape(-1, channels))


class VoiceEngine:
    """Serialize lifecycle and inference; return false/empty results on operational errors."""

    def __init__(self, *, model_directory: str | Path | None = None) -> None:
        self._settings = config.get_voice_settings()
        self._voice_profile = config.get_voice_profile()
        if not re.fullmatch(r"es_(?:ES|MX)-[A-Za-z0-9_]+-(?:x_low|low|medium|high)", self._voice_profile):
            raise ValueError("La voz debe ser un ID Piper español válido.")
        self._model_directory = Path(model_directory) if model_directory is not None else ROOT / ".runtime/voice_models"
        self._stt: Any = None
        self._tts: Any = None
        self._lock = threading.RLock()
        self.last_error: str | None = None

    def _event(self, event: str, success: bool, started: float) -> None:
        logger.info("timestamp=%s evento=%s dispositivo=cpu resultado=%s duracion_s=%.3f",
                    datetime.now(timezone.utc).isoformat(timespec="milliseconds"), event,
                    "correcto" if success else "fallido", time.perf_counter() - started)

    def load_stt(self) -> bool:
        """Lazily load the configured Whisper model from the prepared local CPU/int8 cache."""
        started = time.perf_counter()
        with self._lock:
            try:
                self.last_error = None
                if self._stt is None:
                    candidate = WhisperModel(
                        self._settings["stt_model"], device="cpu", compute_type="int8",
                        download_root=str(self._model_directory / "whisper"),
                        local_files_only=True,
                    )
                    if candidate.model.device != "cpu":
                        raise RuntimeError("STT no confirmó el dispositivo CPU.")
                    self._stt = candidate
                self._event("carga_stt", True, started)
                return True
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("No se pudo cargar STT local en CPU/int8.")
                self._event("carga_stt", False, started)
                return False

    def unload_stt(self) -> bool:
        """Release the CPU model reference after any in-flight transcription finishes."""
        started = time.perf_counter()
        with self._lock:
            try:
                self._stt = None
                gc.collect()
                self._event("descarga_stt", True, started)
                return True
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Fallo controlado al liberar STT.")
                self._event("descarga_stt", False, started)
                return False

    def is_stt_loaded(self) -> bool:
        """Read the observed STT lifecycle state under the inference lock."""
        with self._lock:
            return self._stt is not None

    def transcribe(self, audio_input: Any) -> str:
        """Transcribe PCM WAV, audio arrays or int16 buffers in Spanish, without PyAV.

        Plain arrays/buffers are assumed to be 16 kHz; (sample_rate, samples)
        also accepts the result of AcousticDetector.record at another rate.
        Channel arrays use shape (frames, channels). PCM WAV supplies its own
        rate; channels are averaged and basic linear interpolation yields mono
        float32 at 16 kHz. Compressed files and file-like objects are unsupported.
        Whisper always receives an ndarray, never a filename or file-like object.
        """
        started = time.perf_counter()
        with self._lock:
            try:
                self.last_error = None
                if isinstance(audio_input, (str, os.PathLike)):
                    path = Path(audio_input)
                    if path.suffix.lower() != ".wav" or not path.is_file():
                        raise ValueError("Se requiere la ruta de un WAV existente.")
                    sample_rate, samples = _read_pcm_wav(path)
                else:
                    if (isinstance(audio_input, tuple) and len(audio_input) == 2 and np.isscalar(audio_input[0])
                            and (isinstance(audio_input[1], (bytes, bytearray, memoryview)) or not np.isscalar(audio_input[1]))):
                        sample_rate, samples = audio_input
                    else:
                        sample_rate, samples = 16000, audio_input
                    samples = _normalize_audio(samples)
                audio = _resample_linear(samples, sample_rate)
                if not self.load_stt():
                    self._event("transcripcion", False, started)
                    return ""
                segments, _ = self._stt.transcribe(audio, language="es", beam_size=5)
                text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
                self._event("transcripcion", True, started)
                return text
            except Exception as exc:
                self.last_error = str(exc) or f"Audio inválido o incompleto ({type(exc).__name__})."
                logger.exception("Fallo controlado al transcribir audio en español.")
                self._event("transcripcion", False, started)
                return ""

    def load_tts(self) -> bool:
        """Lazily load the already-downloaded Piper voice with a verified CPU session."""
        started = time.perf_counter()
        with self._lock:
            try:
                self.last_error = None
                if self._tts is None:
                    path = self._model_directory / "piper" / f"{self._voice_profile}.onnx"
                    candidate = PiperVoice.load(path, use_cuda=False)
                    if candidate.session.get_providers() != ["CPUExecutionProvider"]:
                        raise RuntimeError("Piper debe usar exclusivamente CPUExecutionProvider.")
                    if self._settings["tts_speaker_id"] >= candidate.config.num_speakers:
                        raise ValueError("El speaker elegido no existe en la voz Piper cargada.")
                    self._tts = candidate
                self._event("carga_tts", True, started)
                return True
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("No se pudo cargar la voz Piper local en CPU.")
                self._event("carga_tts", False, started)
                return False

    def unload_tts(self) -> bool:
        """Release the CPU voice reference after any in-flight synthesis finishes."""
        started = time.perf_counter()
        with self._lock:
            try:
                self._tts = None
                gc.collect()
                self._event("descarga_tts", True, started)
                return True
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Fallo controlado al liberar TTS.")
                self._event("descarga_tts", False, started)
                return False

    def is_tts_loaded(self) -> bool:
        """Read the observed TTS lifecycle state under the synthesis lock."""
        with self._lock:
            return self._tts is not None

    def synthesize(self, text: str, output_path: str | None = None) -> str:
        """Write an atomic PCM WAV with chosen Piper settings and internal-only aliases.

        The caller's original text, product name and event logs remain unchanged.
        Only a local copy passed to Piper gets case-sensitive whole-word aliases.
        """
        started = time.perf_counter()
        temporary_path: Path | None = None
        with self._lock:
            try:
                self.last_error = None
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("El texto de síntesis debe ser no vacío.")
                destination = Path(output_path).resolve() if output_path is not None else ROOT / ".runtime/tts_output.wav"
                if destination.suffix.lower() != ".wav":
                    raise ValueError("La salida de síntesis debe tener extensión WAV.")
                if not self.load_tts():
                    self._event("sintesis", False, started)
                    return ""
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".wav", delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                with wave.open(str(temporary_path), "wb") as wav_file:
                    synthesis = SynthesisConfig(
                        speaker_id=self._settings["tts_speaker_id"],
                        length_scale=self._settings["tts_length_scale"],
                        noise_scale=self._settings["tts_noise_scale"],
                        noise_w_scale=self._settings["tts_noise_w_scale"],
                    )
                    synthesis_text = _apply_pronunciation_aliases(text, self._settings["tts_pronunciation_aliases"])
                    self._tts.synthesize_wav(synthesis_text, wav_file, syn_config=synthesis)
                with wave.open(str(temporary_path), "rb") as wav_file:
                    if wav_file.getnframes() <= 0 or wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2 or wav_file.getframerate() <= 0:
                        raise ValueError("Piper no produjo un WAV PCM mono int16 válido.")
                os.replace(temporary_path, destination)
                temporary_path = None
                self._event("sintesis", True, started)
                return str(destination)
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Fallo controlado al sintetizar voz con Piper.")
                self._event("sintesis", False, started)
                return ""
            finally:
                if temporary_path is not None:
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError:
                        logger.exception("No se pudo retirar el WAV temporal fallido: %s.", temporary_path)
