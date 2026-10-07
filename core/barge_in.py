"""Detect sustained VAD-positive speech above an RMS threshold on a worker thread."""

from __future__ import annotations

from contextlib import closing
import logging
import math
import threading

from core.acoustic_detector import AcousticDetector

logger = logging.getLogger(__name__)


class BargeInDetector:
    """Latch an acoustic interruption; do not stop playback or claim echo cancellation."""

    def __init__(self, acoustic_detector: AcousticDetector | None = None, *,
                 energy_threshold: float = 0.02, consecutive_frames: int = 3) -> None:
        if isinstance(energy_threshold, bool) or not isinstance(energy_threshold, (int, float)) or not math.isfinite(energy_threshold) or not 0 < energy_threshold <= 1:
            raise ValueError("El umbral RMS debe estar entre 0 (excluido) y 1.")
        if type(consecutive_frames) is not int or consecutive_frames <= 0:
            raise ValueError("El número de bloques consecutivos debe ser un entero positivo.")
        self._acoustic = acoustic_detector if acoustic_detector is not None else AcousticDetector()
        self._energy_threshold = energy_threshold
        self._consecutive_frames = consecutive_frames
        self._stop = threading.Event()
        self._interrupted = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    def start_monitoring(self) -> None:
        """Start at most one background capture; reset the latch only for a new run."""
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._interrupted.clear()
            self.last_error = None
            self._thread = threading.Thread(target=self._monitor, name="EON-BargeIn", daemon=True)
            try:
                self._thread.start()
            except Exception as exc:
                self.last_error = str(exc)
                self._thread = None
                logger.exception("No se pudo iniciar el monitor de interrupción.")

    def _monitor(self) -> None:
        try:
            consecutive = 0
            with closing(self._acoustic.iter_frames(frame_ms=30, stop_event=self._stop)) as frames:
                for frame in frames:
                    if self._stop.is_set():
                        break
                    energetic = self._acoustic.pcm_rms(frame) >= self._energy_threshold
                    speech = self._acoustic.is_speech(frame, self._acoustic.sample_rate)
                    consecutive = consecutive + 1 if energetic and speech else 0
                    if consecutive >= self._consecutive_frames:
                        self._interrupted.set()
                        logger.info("Interrupción acústica detectada por VAD y energía RMS.")
                        break
            if self._acoustic.last_error:
                self.last_error = self._acoustic.last_error
                logger.error("Monitor de interrupción detenido por fallo de captura: %s.", self.last_error)
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Fallo controlado en el monitor de interrupción.")

    def stop_monitoring(self) -> None:
        """Request cancellation and join outside the state lock with a two-second bound."""
        with self._state_lock:
            self._stop.set()
            worker = self._thread
        if worker is not None and worker is not threading.current_thread():
            try:
                worker.join(timeout=2.0)
                if worker.is_alive():
                    self.last_error = "El monitor no terminó dentro de dos segundos."
                    logger.error(self.last_error)
                else:
                    with self._state_lock:
                        if self._thread is worker:
                            self._thread = None
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Fallo controlado al detener el monitor de interrupción.")

    def is_interrupted(self) -> bool:
        """Read the thread-safe latch without waiting for microphone capture."""
        return self._interrupted.is_set()
