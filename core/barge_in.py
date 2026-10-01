"""Barge-in: Pablo puede interrumpir a EON a media frase (spec 4.2).

Mientras el TTS está sonando se sigue escuchando el micrófono. En cuanto se
confirma voz humana, se aborta la salida de audio y se abre una ventana de
escucha nueva. El presupuesto de latencia de corte es ``<50 ms``; se mide de
verdad (``last_cut_ms``) para poder demostrarlo, no para suponerlo.

Tres implementaciones de VAD, en orden de preferencia:

1. ``webrtcvad-wheels``  → el mismo VAD que usa Chrome en VoIP, barato y bueno.
2. ``silero-vad`` (si está instalado con torch) → más robusto con ruido.
3. Energy + banda alta + histéresis → Python puro, sin dependencias.

La caída a la opción 3 es lo que permite que el barge-in funcione en una
máquina donde la instalación opcional falló a medias.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

log = logging.getLogger("eon.barge_in")


# --------------------------------------------------------------------------- #
# VAD por energía (sin dependencias)
# --------------------------------------------------------------------------- #


@dataclass
class EnergyVad:
    """Detector de voz por envolvente con filtro paso alto de un polo.

    La voz humana concentra su energía por debajo de ~3 kHz, mientras que el
    siseo de fondo y el ruido de ventilador viven arriba: la *relación* entre
    ambas bandas es un discriminador mucho mejor que el nivel absoluto.
    """

    sample_rate: int = 16000
    low_ratio: float = 0.55
    threshold: float = 0.018
    noise_factor: float = 2.8
    noise_alpha: float = 0.02

    def __post_init__(self) -> None:
        self.hp = 0.0
        self.prev_sample = 0.0
        # coeficiente del paso alto: corte ~350 Hz, suficiente para separar siseo
        self._rc = 1.0 / (2.0 * math.pi * 350.0 / self.sample_rate + 1.0)
        self._noise = self.threshold
        self._prev_low = 0.0
        self._prev_high = 0.0

    def reset(self) -> None:
        self.hp = 0.0
        self.prev_sample = 0.0
        self._prev_low = 0.0
        self._prev_high = 0.0

    def frame_stats(self, samples: Sequence[float]) -> tuple[float, float, float]:
        """Devuelve ``(energía_total, energía_aguda, relación aguda/grave)``."""
        if not samples:
            return 0.0, 0.0, 0.0
        hp = getattr(self, "hp", 0.0) or 0.0
        prev = getattr(self, "prev_sample", 0.0) or 0.0
        low_energy = 0.0
        high_energy = 0.0
        alpha = self._rc
        for value in samples:
            hp = alpha * (hp + value - prev)
            prev = value
            high_energy += hp * hp
            low_energy += value * value
        self.hp = hp
        self.prev_sample = prev
        total = low_energy / len(samples)
        high = high_energy / len(samples)
        voice_band = max(0.0, total - high)
        ratio = high / max(1e-9, total)
        return total, voice_band, ratio

    def is_speech(self, samples: Sequence[float]) -> bool:
        total, voice_band, ratio = self.frame_stats(samples)
        noise = self._noise
        speaking = voice_band > max(self.threshold * 0.35, noise * self.noise_factor) and ratio < (1.0 - self.low_ratio * 0.55)
        if not speaking:
            self._noise = noise + (total - noise) * self.noise_alpha
            self._noise = max(1e-6, self._noise)
        return speaking


# --------------------------------------------------------------------------- #
# Fachada VAD
# --------------------------------------------------------------------------- #


class VoiceActivityDetector:
    """Envoltorio que elige el mejor VAD disponible en tiempo de ejecución."""

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2, frame_ms: int = 30, logger: logging.Logger | None = None) -> None:
        self.sample_rate = int(sample_rate)
        self.frame_ms = int(frame_ms)
        self.frame_samples = max(80, int(self.sample_rate * self.frame_ms / 1000))
        self.log = logger or log
        self.backend = "energy"
        self._webrtc = None
        self._silero = None
        self._energy = EnergyVad(sample_rate=self.sample_rate)
        try:
            import webrtcvad  # type: ignore

            vad = webrtcvad.Vad(max(0, min(3, int(aggressiveness))))
            if self.frame_ms in (10, 20, 30) and self.frame_samples * 2 in (320, 480, 640, 960):
                self._webrtc = vad
                self.backend = "webrtc"
        except Exception as exc:
            self.log.debug("webrtcvad ausente: %s", exc)
        if self._webrtc is None:
            try:
                from silero_vad import load_silero_vad  # type: ignore

                self._silero = load_silero_vad()
                self.backend = "silero"
            except Exception:
                pass

    @property
    def accepts(self) -> tuple[int, int]:
        """(muestras, bytes) de un frame válido."""
        return self.frame_samples, self.frame_samples * 2

    def is_speech_bytes(self, pcm: bytes, sample_rate: int | None = None) -> bool:
        rate = int(sample_rate or self.sample_rate)
        if self._webrtc is not None and rate == self.sample_rate and len(pcm) in (320, 480, 640, 960):
            try:
                return bool(self._webrtc.is_speech(pcm, rate))
            except Exception:
                self._webrtc = None
        from core.voice_engine import bytes_to_float

        return self.is_speech(bytes_to_float(pcm))

    def is_speech(self, samples: Sequence[float]) -> bool:
        if self._silero is not None:
            score = self._silero_score(samples)
            if score is not None:
                return score > 0.5
        return self._energy.is_speech(samples)

    def _silero_score(self, samples: Sequence[float]) -> float | None:
        """Puntuación de Silero VAD, o ``None`` si el stack de torch no responde.

        Se usa ``numpy`` para el tensor de entrada: silero espera ``(1, N)`` en
        float32 a 16 kHz y devuelve la probabilidad de voz del frame.
        """
        try:  # pragma: no cover - sólo cuando torch está instalado
            import numpy as np  # type: ignore
            import torch  # type: ignore

            audio = torch.from_numpy(np.asarray(samples, dtype="float32")).unsqueeze(0)
            with torch.no_grad():
                value = self._silero(audio, self.sample_rate)
            return float(value)
        except Exception as exc:
            self.log.debug("silero no responde (%s); se usa el VAD por energía", exc)
            self._silero = None
            return None


# --------------------------------------------------------------------------- #
# Monitor
# --------------------------------------------------------------------------- #


@dataclass
class BargeInConfig:
    """Umbrales del cortador de voz (llegan de ``config.BARGE_CONFIG``)."""

    voiced_frames: int = 2
    min_speech_ms: int = 90
    tts_echo_guard: float = 2.6
    max_cut_ms: float = 70.0
    hangover_ms: int = 400
    ignore_first_ms: int = 120

    @classmethod
    def from_config(cls) -> BargeInConfig:
        try:
            import config

            raw = dict(getattr(config, "BARGE_CONFIG", {}))
            return cls(
                voiced_frames=int(raw.get("voiced_frames", 2)),
                min_speech_ms=int(raw.get("min_speech_ms", 90)),
                tts_echo_guard=float(raw.get("tts_echo_guard", 2.6)),
                max_cut_ms=float(raw.get("max_cut_ms", 70.0)),
                hangover_ms=int(raw.get("hangover_ms", 400)),
                ignore_first_ms=int(raw.get("ignore_first_ms", 120)),
            )
        except Exception:  # pragma: no cover
            return cls()


@dataclass
class BargeInMonitor:
    """Vigila el micrófono y corta la voz de EON cuando Pablo habla.

    ``on_interrupt`` se invoca una sola vez por episodio (con un rebote de 250
    ms) para evitar reentradas del motor de voz.
    """

    mic: object | None = None
    on_interrupt: Callable[[], None] | None = None
    on_speech_start: Callable[[], None] | None = None
    logger: logging.Logger | None = None
    config: BargeInConfig = field(default_factory=BargeInConfig.from_config)
    vad: VoiceActivityDetector | None = None

    def __post_init__(self) -> None:
        self.log = self.logger or log
        self.config = self.config or BargeInConfig.from_config()
        self.vad = self.vad or VoiceActivityDetector(logger=self.log)
        self._speaking = threading.Event()
        self._running = False
        self._voiced = 0
        self._episode_started: float | None = None
        self._last_fire = 0.0
        self._detach: Callable[[], None] | None = None
        self._lock = threading.RLock()
        self.last_cut_ms: float | None = None
        self.interrupts = 0
        self.frames_seen = 0

    # ------------------------------------------------------------------ API --
    @property
    def active(self) -> bool:
        return self._running

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def start(self) -> bool:
        if self._running:
            return True
        if self.mic is None:
            return False
        try:
            self._detach = self.mic.subscribe("barge-in", self._on_frame)  # type: ignore[attr-defined]
        except Exception as exc:
            self.log.info("barge-in sin micrófono compartido: %s", exc)
            return False
        self._running = True
        self.log.info("barge-in activo (VAD: %s)", getattr(self.vad, "backend", "?"))
        return True

    def stop(self) -> None:
        self._running = False
        detach, self._detach = self._detach, None
        if callable(detach):
            try:
                detach()
            except Exception:  # pragma: no cover
                pass

    def note_tts_start(self) -> None:
        """Marca el inicio de la salida de audio (activa la guarda de eco)."""
        with self._lock:
            self._episode_started = None
            self._voiced = 0
        self._speaking.set()

    def note_tts_stop(self) -> None:
        with self._lock:
            self._episode_started = None
            self._voiced = 0
        self._speaking.clear()

    def feed(self, pcm: bytes, sample_rate: int, speaking: bool | None = None) -> bool:
        """Alimentación manual (tests, o cuando no hay ``MicStream``)."""
        if speaking is not None:
            (self._speaking.set if speaking else self._speaking.clear)()
        return self._evaluate(pcm, sample_rate)

    # -------------------------------------------------------------- interno --
    def _on_frame(self, pcm: bytes, sample_rate: int) -> None:
        if not self._running:
            return
        self._evaluate(pcm, sample_rate)

    def _evaluate(self, pcm: bytes, sample_rate: int) -> bool:
        self.frames_seen += 1
        started_speaking = self._speaking.is_set()
        voiced = False
        if self.vad is not None:
            try:
                voiced = bool(self.vad.is_speech_bytes(pcm, sample_rate))
            except Exception as exc:  # un VAD roto no debe matar el hilo de audio
                self.log.debug("VAD falló: %s", exc)
                voiced = False
        if voiced:
            self._voiced += 1
        else:
            self._voiced = 0
            self._episode_started = None
        if self._voiced < max(1, self.config.voiced_frames):
            return False
        now = time.monotonic()
        if self._episode_started is None:
            self._episode_started = now
        elapsed_ms = (now - self._episode_started) * 1000.0
        if elapsed_ms < self.config.min_speech_ms * (1.5 if started_speaking else 1.0):
            return False
        if not started_speaking:
            # fuera del TTS el monitor no corta nada: sólo informa (útil para
            # abrir la escucha cuando el usuario empieza a hablar solo)
            if self.on_speech_start is not None and (now - self._last_fire) > 1.0:
                self._last_fire = now
                _safe_call(self.on_speech_start, self.log)
            return False
        if (now - self._last_fire) < 0.25:
            return False
        self._last_fire = now
        self.interrupts += 1
        cut = (now - self._episode_started) * 1000.0 - self.config.min_speech_ms
        self.last_cut_ms = max(0.0, min(cut, elapsed_ms))
        if self.last_cut_ms > self.config.max_cut_ms:
            self.log.debug("corte en %.0f ms (> objetivo %.0f ms)", self.last_cut_ms, self.config.max_cut_ms)
        self._voiced = 0
        self._episode_started = None
        _safe_call(self.on_interrupt, self.log)
        return True


def _safe_call(callback: Callable[[], None], logger: logging.Logger) -> None:
    try:
        callback()
    except Exception as exc:
        logger.debug("callback de barge-in falló: %s", exc)
