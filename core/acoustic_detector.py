"""Capture mono PCM, apply WebRTC VAD and detect one CPU-only wake phrase."""

from __future__ import annotations

from contextlib import closing
import logging
import math
from pathlib import Path
import queue
import threading
import time
from typing import Any, Iterator

import numpy as np
import sounddevice as sd
import webrtcvad

import config

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
_MICROPHONE_LOCK = threading.Lock()


class AcousticDetector:
    """Return controlled empty/false results for capture and VAD failures."""

    def __init__(self, sample_rate: int | None = None, vad_aggressiveness: int | None = None,
                 device: int | str | None = None) -> None:
        settings = config.get_voice_settings()
        self.sample_rate = settings["sample_rate"] if sample_rate is None else sample_rate
        aggressiveness = settings["vad_aggressiveness"] if vad_aggressiveness is None else vad_aggressiveness
        if type(self.sample_rate) is not int or self.sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("La frecuencia debe ser 8000, 16000, 32000 o 48000 Hz.")
        if type(aggressiveness) is not int or aggressiveness not in range(4):
            raise ValueError("La agresividad VAD debe ser un entero entre 0 y 3.")
        self.device = device
        self.last_error: str | None = None
        self._vad = webrtcvad.Vad(aggressiveness)
        self._vad_lock = threading.Lock()

    def list_devices(self) -> list[dict[str, Any]]:
        """Enumerate real PortAudio devices without opening the microphone."""
        try:
            self.last_error = None
            return [{"index": index, **dict(device)} for index, device in enumerate(sd.query_devices())]
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("No se pudieron enumerar los dispositivos de audio.")
            return []

    @staticmethod
    def pcm_rms(audio_frame: bytes) -> float:
        """Return normalized mono int16 RMS; reject incomplete PCM samples."""
        if not isinstance(audio_frame, bytes) or len(audio_frame) % 2:
            raise ValueError("El audio debe ser PCM mono int16 con muestras completas.")
        samples = np.frombuffer(audio_frame, dtype="<i2").astype(np.float64)
        return float(np.sqrt(np.mean(samples * samples)) / 32768.0) if samples.size else 0.0

    def iter_frames(self, frame_ms: int = 30, stop_event: threading.Event | None = None,
                    timeout_seconds: float | None = None) -> Iterator[bytes]:
        """Stream bounded queued PCM frames, checking cancellation every 100 ms."""
        if type(frame_ms) is not int or frame_ms not in (10, 20, 30, 80):
            raise ValueError("Los bloques deben ser de 10, 20, 30 u 80 ms.")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("El timeout debe ser un número finito positivo.")
        self.last_error = None
        if not _MICROPHONE_LOCK.acquire(blocking=False):
            self.last_error = "El micrófono está ocupado por otra captura de EON."
            logger.error(self.last_error)
            return
        frames: queue.Queue[bytes] = queue.Queue(maxsize=50)
        frame_samples = self.sample_rate * frame_ms // 1000

        def callback(indata: np.ndarray, count: int, timing: Any, status: Any) -> None:
            try:
                if status:
                    logger.warning("Estado de captura PortAudio: %s.", status)
                frames.put_nowait(np.asarray(indata, dtype="<i2").reshape(-1).tobytes())
            except queue.Full:
                self.last_error = "La captura perdió audio por saturación de la cola."
                logger.error(self.last_error)
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Falló el callback de captura de audio.")

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="int16",
                                device=self.device, blocksize=frame_samples, callback=callback):
                started = last_frame = time.monotonic()
                deadline = started + timeout_seconds if timeout_seconds is not None else None
                while stop_event is None or not stop_event.is_set():
                    remaining = deadline - time.monotonic() if deadline is not None else 0.1
                    if deadline is not None and remaining <= 0:
                        break
                    if self.last_error:
                        break
                    try:
                        frame = frames.get(timeout=min(0.1, remaining))
                    except queue.Empty:
                        if time.monotonic() - last_frame > 1.0:
                            self.last_error = "El dispositivo no entrega audio."
                            logger.error(self.last_error)
                            break
                        continue
                    last_frame = time.monotonic()
                    if len(frame) != frame_samples * 2:
                        self.last_error = "PortAudio devolvió un bloque PCM de tamaño inesperado."
                        logger.error(self.last_error)
                        break
                    yield frame
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Fallo controlado al abrir o capturar el micrófono.")
        finally:
            _MICROPHONE_LOCK.release()

    def record(self, seconds: float) -> tuple[int, Any]:
        """Capture exactly the requested sample count, or return an empty int16 array."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("La duración debe ser un número finito positivo.")
        sample_count = max(1, round(seconds * self.sample_rate))
        collected: list[bytes] = []
        total_bytes = 0
        with closing(self.iter_frames(timeout_seconds=seconds + 2.0)) as stream:
            for frame in stream:
                collected.append(frame)
                total_bytes += len(frame)
                if total_bytes >= sample_count * 2:
                    break
        if total_bytes < sample_count * 2 or self.last_error:
            self.last_error = self.last_error or "La grabación terminó sin suficientes muestras."
            logger.error("Grabación fallida: %s", self.last_error)
            return self.sample_rate, np.empty(0, dtype=np.int16)
        audio = np.frombuffer(b"".join(collected)[:sample_count * 2], dtype="<i2").copy()
        return self.sample_rate, audio

    def is_speech(self, audio_frame: bytes, sample_rate: int) -> bool:
        """Apply real WebRTC VAD to valid 10/20/30 ms int16 frames; log invalid input."""
        try:
            if type(sample_rate) is not int or sample_rate not in (8000, 16000, 32000, 48000):
                raise ValueError("Frecuencia no admitida por VAD.")
            if not isinstance(audio_frame, bytes) or len(audio_frame) not in {
                sample_rate * milliseconds // 1000 * 2 for milliseconds in (10, 20, 30)
            }:
                raise ValueError("VAD requiere PCM mono int16 de 10, 20 o 30 ms.")
            with self._vad_lock:
                return bool(self._vad.is_speech(audio_frame, sample_rate))
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Fallo controlado al evaluar VAD.")
            return False

    def get_input_level(self, seconds: float) -> float:
        """Measure normalized RMS on real captured PCM; return zero on capture failure."""
        _, audio = self.record(seconds)
        return self.pcm_rms(audio.tobytes()) if audio.size else 0.0


class WakeWordDetector:
    """Load one known ONNX wake phrase on CPU; never download models implicitly."""

    def __init__(self, model_name: str, *, acoustic_detector: AcousticDetector | None = None,
                 model_directory: str | Path | None = None, threshold: float = 0.5) -> None:
        import openwakeword

        if not isinstance(model_name, str) or model_name not in openwakeword.MODELS:
            raise ValueError("El ID wake-word no pertenece al catálogo instalado de openWakeWord.")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError("El umbral wake-word debe estar entre 0 (excluido) y 1.")
        self.model_name = model_name
        self.threshold = threshold
        self.last_error: str | None = None
        self._model: Any = None
        self._listen_lock = threading.Lock()
        self._acoustic = acoustic_detector if acoustic_detector is not None else AcousticDetector(sample_rate=16000)
        if self._acoustic.sample_rate != 16000:
            raise ValueError("openWakeWord requiere audio a 16000 Hz.")
        directory = Path(model_directory) if model_directory is not None else ROOT / ".runtime/voice_models/wake_word"
        filename = Path(openwakeword.MODELS[model_name]["model_path"]).with_suffix(".onnx").name
        try:
            paths = [directory / filename, directory / "melspectrogram.onnx", directory / "embedding_model.onnx"]
            if any(not path.is_file() for path in paths):
                raise FileNotFoundError("Faltan los modelos ONNX locales de wake-word o sus extractores.")
            candidate = openwakeword.Model(
                wakeword_models=[str(paths[0])], inference_framework="onnx", device="cpu", ncpu=1,
                melspec_model_path=str(paths[1]), embedding_model_path=str(paths[2]),
            )
            sessions = [*candidate.models.values(), candidate.preprocessor.melspec_model, candidate.preprocessor.embedding_model]
            if len(candidate.models) != 1 or any(session.get_providers() != ["CPUExecutionProvider"] for session in sessions):
                raise RuntimeError("Las sesiones wake-word deben usar exclusivamente CPUExecutionProvider.")
            self._model = candidate
            logger.info("timestamp=%s evento=carga_wake_word modelo=%s dispositivo=cpu resultado=correcto",
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), model_name)
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("No se pudo cargar el wake-word local en CPU: %s.", model_name)

    @staticmethod
    def available_models() -> list[str]:
        """Return real built-in IDs from the installed library, not file availability."""
        import openwakeword

        return sorted(openwakeword.MODELS)

    def is_available(self) -> bool:
        """Report whether the selected model and both CPU feature sessions loaded."""
        return self._model is not None

    def listen_once(self, timeout_seconds: float) -> bool:
        """Listen to real 80 ms frames until a detection or deadline; fail closed."""
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("El timeout wake-word debe ser finito y positivo.")
        if not self.is_available():
            logger.error("Wake-word no disponible: %s.", self.last_error)
            return False
        with self._listen_lock:
            try:
                self.last_error = None
                self._model.reset()
                with closing(self._acoustic.iter_frames(frame_ms=80, timeout_seconds=timeout_seconds)) as frames:
                    for frame in frames:
                        prediction = self._model.predict(np.frombuffer(frame, dtype="<i2"))
                        if any(float(score) >= self.threshold for score in prediction.values()):
                            logger.info("Wake-word detectado: modelo=%s.", self.model_name)
                            return True
                if self._acoustic.last_error:
                    self.last_error = self._acoustic.last_error
                    logger.error("Escucha wake-word fallida: %s.", self.last_error)
                return False
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Fallo controlado en escucha wake-word.")
                return False
