"""Motor de voz de EON: escucha permanente, STT y TTS con sincronía labial (spec 3 y 4.2).

Arquitectura de un solo micrófono
---------------------------------
``MicStream`` abre **una única** entrada de audio a 16 kHz mono y la reparte a
los consumidores (palabra de activación, detector de palmadas, VAD de barge-in y
transcripción). Abrir el micro varias veces en Windows reparte el dispositivo,
introduce retardos y a veces lo bloquea del todo; aquí cada oyente recibe las
mismas muestras.

Degrada­ción elegante
--------------------
Cada subsistema comprueba su backend en tiempo de ejecución y expone
``available()``:

* wake word: ``openwakeword`` -> si no, pulsador/hotkey + palmadas;
* STT: ``faster-whisper`` (``base`` en INT8 sobre CPU, ~250 MB) -> si no, dictado
  de Windows vía ``PowerShell`` -> si no, entrada escrita en el notch;
* TTS: ``piper`` (voz en español) -> ``kokoro`` -> ``SAPI``/``espeak`` -> texto
  mudo en el notch.

Ninguna de esas ausencias cierra la aplicación: sólo cambian las capacidades.
"""

from __future__ import annotations

import array
import base64
import io
import logging
import math
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("eon.voice")

_INT16_MAX = 32768.0


# --------------------------------------------------------------------------- #
# Utilidades DSP puras (testeables sin hardware)
# --------------------------------------------------------------------------- #


def bytes_to_float(pcm: bytes | bytearray | memoryview) -> list[float]:
    """convierte PCM s16le en muestras ``-1.0 .. 1.0``."""
    data = array.array("h")
    data.frombytes(bytes(pcm))
    return [sample / _INT16_MAX for sample in data]


def float_to_bytes(samples: Iterable[float]) -> bytes:
    """Lo inverso, con saturación (nunca desborda el rango del DAC)."""
    out = array.array("h")
    for value in samples:
        clipped = max(-1.0, min(1.0, float(value)))
        out.append(int(round(clipped * (_INT16_MAX - 1))))
    return out.tobytes()


def rms(samples: Sequence[float]) -> float:
    """Root Mean Square de una ventana de muestras flotantes."""
    if not samples:
        return 0.0
    total = 0.0
    for value in samples:
        total += value * value
    return math.sqrt(total / len(samples))


def compute_envelope(samples: Sequence[float], bins: int = 96, floor: float = 0.012) -> list[float]:
    """Envolvente de amplitud normalizada para mover la boca del personaje.

    Se divide el audio en ``bins`` ventanas, se calcula el RMS de cada una, se
    normaliza contra el percentil alto (no contra el máximo: un clic suelto no
    debe dejar la boca quieta el resto de la frase) y se alisa con un promedio
    de tres muestras.
    """
    if bins < 1 or not samples:
        return []
    length = len(samples)
    size = max(1, length // bins)
    raw: list[float] = []
    for index in range(bins):
        start = index * size
        chunk = samples[start : start + size]
        if not chunk:
            raw.append(0.0)
            continue
        raw.append(rms(chunk))
    if not raw:
        return []
    ordered = sorted(raw)
    reference = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] or 1.0
    norm = [max(0.0, min(1.0, (value - floor) / max(1e-6, reference - floor))) for value in raw]
    smoothed: list[float] = []
    for index in range(len(norm)):
        window = norm[max(0, index - 1) : index + 2]
        smoothed.append(sum(window) / len(window))
    return smoothed


def trim_silence(samples: Sequence[float], threshold: float = 0.008, pad: int = 320) -> list[float]:
    """Recorta el silencio de los extremos y deja ``pad`` muestras de aire."""
    if not samples:
        return []
    start = 0
    while start < len(samples) and abs(samples[start]) < threshold:
        start += 1
    end = len(samples)
    while end > start and abs(samples[end - 1]) < threshold:
        end -= 1
    if start >= end:
        return []
    lo = max(0, start - pad)
    hi = min(len(samples), end + pad)
    return list(samples[lo:hi])


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|(?<=:)\s+")


def split_sentences(text: str, max_len: int = 220) -> list[str]:
    """Parte la respuesta en frases para ir hablando mientras se genera.

    Hablar la respuesta entera sólo al final sumaría 3-6 segundos de latencia
    percibida; con esto EON empieza a sonar con la primera frase completa.
    """
    if not text:
        return []
    chunks: list[str] = []
    for sentence in (part.strip() for part in _SENTENCE_SPLIT.split(text)):
        if not sentence:
            continue
        if len(sentence) <= max_len:
            chunks.append(sentence)
            continue
        words = sentence.split()
        buffer: list[str] = []
        size = 0

        def flush() -> None:
            nonlocal buffer, size
            if buffer:
                chunks.append(" ".join(buffer))
                buffer, size = [], 0

        for word in words:
            if len(word) > max_len:  # una palabra-desastre (URLs, JSON pegado) no puede esperar al hueco
                flush()
                chunks.extend(word[i : i + max_len] for i in range(0, len(word), max_len))
                continue
            if size + len(word) + 1 > max_len:
                flush()
            buffer.append(word)
            size += len(word) + 1
        flush()
    return chunks


# --------------------------------------------------------------------------- #
# Micrófono compartido
# --------------------------------------------------------------------------- #


@dataclass
class FrameSink:
    """Suscriptor al flujo de micrófono."""

    name: str
    callback: Callable[[bytes, int], None]
    enabled: Callable[[], bool] = lambda: True
    errors: int = 0


class MicStream:
    """Flujo único de captura que reparte frames int16 a varios consumidores.

    Si el dispositivo falla (micrófono desenchufado, permisos denegados), el
    hilo de captura muere con un aviso y los consumidores siguen vivos pero sin
    datos: la aplicación no se cae, sólo deja de oír.
    """

    def __init__(
        self,
        sample_rate: int | None = None,
        frame_ms: int | None = None,
        device: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        try:
            import config

            self.sample_rate = int(sample_rate or config.SAMPLE_RATE)
            self.frame_ms = int(frame_ms or config.FRAME_MS)
            self.device = device if device is not None else config.INPUT_DEVICE
        except Exception:  # pragma: no cover
            self.sample_rate = int(sample_rate or 16000)
            self.frame_ms = int(frame_ms or 30)
            self.device = None
        self.log = logger or log
        self.frame_samples = max(160, int(self.sample_rate * self.frame_ms / 1000))
        self._sinks: list[FrameSink] = []
        self._lock = threading.RLock()
        self._buffer: deque[float] = deque(maxlen=self.sample_rate * 8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._sounddevice = None
        self._available: bool | None = None
        self.frames_seen = 0
        self.level = 0.0

    # ------------------------------------------------------------------ API --
    def available(self) -> bool:
        if self._available is None:
            try:
                import sounddevice  # type: ignore

                self._sounddevice = sounddevice
                self._available = True
            except Exception as exc:
                self.log.info("sin módulo de audio (sounddevice): %s", exc)
                self._available = False
        return bool(self._available)

    def subscribe(self, name: str, callback: Callable[[bytes, int], None], enabled: Callable[[], bool] | None = None) -> Callable[[], None]:
        sink = FrameSink(name=name, callback=callback, enabled=enabled or (lambda: True))
        with self._lock:
            self._sinks = [item for item in self._sinks if item.name != name]
            self._sinks.append(sink)

        def unsubscribe() -> None:
            with self._lock:
                self._sinks = [item for item in self._sinks if item is not sink]

        return unsubscribe

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        if not self.available():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="eon-mic", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # pragma: no cover
                pass
        self._stream = None
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def recent(self, seconds: float = 4.0) -> list[float]:
        """Últimos ``seconds`` de audio (flotantes) para transcribir un trigger."""
        count = min(len(self._buffer), int(self.sample_rate * seconds))
        if count <= 0:
            return []
        return list(self._buffer)[-count:]

    def level_envelope(self, window: float = 0.12) -> float:
        """Nivel RMS recentrado: alimenta las barras del notch."""
        count = min(len(self._buffer), max(1, int(self.sample_rate * window)))
        if count == 0:
            return 0.0
        return min(1.0, rms(list(self._buffer)[-count:]) * 4.0)

    # -------------------------------------------------------------- interno --
    def _loop(self) -> None:
        sd = self._sounddevice
        assert sd is not None  # garantizado por available()
        blocksize = self.frame_samples
        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=blocksize,
                device=self.device,
                dtype="int16",
                channels=1,
            ) as stream:
                self._stream = stream
                self.log.info("micrófono abierto a %d Hz, bloque de %d muestras", self.sample_rate, blocksize)
                while not self._stop.is_set():
                    data, overflowed = stream.read(blocksize)
                    if overflowed:
                        self.log.debug("overflow en la captura")
                    payload = bytes(data)
                    self.frames_seen += 1
                    samples = bytes_to_float(payload)
                    self.level = min(1.0, rms(samples) * 6.0)
                    self._buffer.extend(samples)
                    with self._lock:
                        sinks = list(self._sinks)
                    for sink in sinks:
                        try:
                            if sink.enabled():
                                sink.callback(payload, self.sample_rate)
                        except Exception as exc:
                            sink.errors += 1
                            self.log.debug("sink %s falló (%d): %s", sink.name, sink.errors, exc)
                            if sink.errors > 50:
                                self.log.warning("sink %s desactivado por errores", sink.name)
                                with self._lock:
                                    if sink in self._sinks:
                                        self._sinks.remove(sink)
        except Exception as exc:
            self.log.warning("micrófono no disponible: %s", exc)
        finally:
            self._stream = None


# --------------------------------------------------------------------------- #
# Wake word
# --------------------------------------------------------------------------- #


@dataclass
class WakeEvent:
    """Una activación reconocida."""

    model: str
    score: float
    at: float


class WakeWordDetector:
    """Palabra de activación con openWakeWord, o modo sin modelo.

    Cuando ``openwakeword`` no está instalado se puede seguir usando EON de dos
    formas: la palmada doble (``core.acoustic_detector``) y el atajo de teclado
    global registrado en ``main.py``. En ambos casos la escucha del micrófono
    sigue activa para el STT.
    """

    def __init__(self, models: Sequence[str] | None = None, threshold: float | None = None, sample_rate: int = 16000, logger: logging.Logger | None = None) -> None:
        try:
            import config

            self.models = tuple(models or config.WAKE_MODEL_CANDIDATES)
            self.threshold = float(threshold if threshold is not None else config.WAKE_THRESHOLD)
            self.refractory = float(config.WAKE_REFRACTORY_S)
            self.sample_rate = int(sample_rate or config.SAMPLE_RATE)
        except Exception:  # pragma: no cover
            self.models = tuple(models or ("hey_sir",))
            self.threshold = 0.62
            self.refractory = 1.2
            self.sample_rate = 16000
        self.log = logger or log
        self._model = None
        self._last: float = 0.0
        self._lock = threading.Lock()
        self._tried = False
        self.last_score = 0.0

    @property
    def ready(self) -> bool:
        self._ensure()
        return self._model is not None

    def _ensure(self) -> None:
        if self._tried:
            return
        self._tried = True
        try:
            from openwakeword.model import Model  # type: ignore

            available = []
            try:
                from openwakeword.utils import download_models  # type: ignore

                download_models(list(self.models))
            except Exception as exc:  # pragma: no cover - sin red o ya bajados
                self.log.debug("openwakeword no pudo preparar modelos: %s", exc)
            for candidate in self.models:
                try:
                    available.append(Model(wakeword_models=[candidate], inference_framework="onnx"))
                    self.chosen = candidate
                    break
                except Exception:
                    continue
            if available:
                self._model = available[0]
                self.log.info("palabra de activación cargada: %s", getattr(self, "chosen", "?"))
            else:
                self.log.warning("sin modelo de wake word: usa la palmada doble o el atajo de teclado")
        except Exception as exc:
            self.log.info("openwakeword no está disponible (%s)", exc)

    def feed(self, pcm: bytes, sample_rate: int) -> WakeEvent | None:
        """Recibe un frame del micrófono y devuelve un evento si hay activación."""
        self._ensure()
        if self._model is None:
            return None
        try:
            prediction = self._model.predict(pcm)
        except Exception as exc:  # el formato de frame no coincide, p.ej.
            self.log.debug("predict falló: %s", exc)
            self._model = None
            return None
        best_name, best_score = "", 0.0
        if isinstance(prediction, dict):
            for name, score in prediction.items():
                if float(score) > best_score:
                    best_name, best_score = str(name), float(score)
        self.last_score = best_score
        now = time.monotonic()
        if best_score >= self.threshold and (now - self._last) > self.refractory:
            self._last = now
            with self._lock:
                try:
                    self._model.reset()
                except Exception:
                    pass
            return WakeEvent(model=best_name, score=best_score, at=now)
        return None

    def reset(self) -> None:
        self._last = 0.0


# --------------------------------------------------------------------------- #
# STT
# --------------------------------------------------------------------------- #


@dataclass
class Transcript:
    """Resultado de una transcripción."""

    text: str
    language: str = ""
    duration_s: float = 0.0
    backend: str = ""
    confidence: float = 1.0

    def is_empty(self) -> bool:
        return not self.text.strip()


class SpeechToText:
    """faster-whisper en CPU/INT8, con dictado de Windows como plan B.

    Whisper se deja en CPU a propósito: ``base`` en INT8 tarda ~0.3-0.6 s en una
    frase corta y libera la VRAM completa para los LLM, que es justo lo que pide
    la Ley de Oro.
    """

    def __init__(self, model_name: str | None = None, device: str | None = None, compute: str | None = None, logger: logging.Logger | None = None) -> None:
        try:
            import config

            self.model_name = model_name or config.WHISPER_MODEL
            self.device = device or config.WHISPER_DEVICE
            self.compute = compute or config.WHISPER_COMPUTE
            self.language = config.WHISPER_LANGUAGE
            self.beam_size = int(config.WHISPER_BEAM_SIZE)
            self.max_seconds = float(config.STT_MAX_SECONDS)
        except Exception:  # pragma: no cover
            self.model_name = "base"
            self.device = "cpu"
            self.compute = "int8"
            self.language = "es"
            self.beam_size = 1
            self.max_seconds = 15.0
        self.log = logger or log
        self._model = None
        self._lock = threading.Lock()
        self._warmed = False

    def available(self) -> bool:
        try:
            import faster_whisper  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def warmup(self) -> None:
        """Precarga el modelo en segundo plano para que la primera frase no tarde 2 s."""
        if self._warmed:
            return
        self._warmed = True
        threading.Thread(target=self._load, name="eon-stt-warm", daemon=True).start()

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel  # type: ignore

                self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute)
                self.log.info("STT listo (faster-whisper %s / %s)", self.model_name, self.compute)
            except Exception as exc:
                self.log.info("faster-whisper no disponible: %s", exc)
                self._model = None
            return self._model

    def transcribe(self, samples: Sequence[float], sample_rate: int = 16000) -> Transcript:
        """Transcribe flotantes ``-1..1``."""
        if not samples:
            return Transcript(text="", backend="none")
        model = self._load()
        if model is None:
            return Transcript(text=self._transcribe_fallback(samples, sample_rate), backend="system")
        try:
            import numpy as np  # type: ignore

            audio = np.asarray(samples[: int(sample_rate * self.max_seconds)], dtype="float32")
            segments, info = model.transcribe(
                audio,
                language=self.language or None,
                beam_size=self.beam_size,
                vad_filter=True,
                word_timestamps=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return Transcript(
                text=text,
                language=getattr(info, "language", "") or "",
                duration_s=float(getattr(info, "duration", 0.0) or 0.0),
                backend="faster-whisper",
                confidence=float(getattr(info, "avg_logprob", -1.0) or -1.0),
            )
        except Exception as exc:
            self.log.warning("transcripción falló: %s", exc)
            return Transcript(text="", backend="error")

    def _transcribe_fallback(self, samples: Sequence[float], sample_rate: int) -> str:
        """Dictado nativo del sistema. Windows tiene uno; lo usamos sin instalar nada."""
        if sys.platform != "win32":
            return ""
        try:  # pragma: no cover - sólo Windows
            wav = _encode_wav(samples, sample_rate)[: 4096]
            # base64 dentro del guión: ni rebanadas de lista con corchetes ni comas
            # que PowerShell no entiende (el intento anterior con ,%s y una lista
            # de Python devolvía siempre un error de sintaxis en el dictado).
            b64 = base64.b64encode(wav).decode("ascii")
            payload = (
                "Add-Type -AssemblyName System.Speech; "
                "$b = [Convert]::FromBase64String('" + b64 + "'); "
                "$ms = New-Object System.IO.MemoryStream(,$b); "
                "$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine; "
                "$r.InstallChoice = [System.Speech.Recognition.InstallChoice]::AllLocales; "
                "$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar)); "
                "$r.SetInput($ms); "
                "$e = $r.Recognize(); if ($e) { $e.Text }"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", payload],
                capture_output=True, text=True, timeout=8, check=False,
            )
            return (result.stdout or "").strip()
        except Exception as exc:
            self.log.debug("dictado de sistema no disponible: %s", exc)
            return ""


def _encode_wav(samples: Sequence[float], sample_rate: int, width: int = 2) -> bytes:
    """Empaqueta flotantes en un WAV mono PCM."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(width)
        handle.setframerate(sample_rate)
        handle.writeframes(float_to_bytes(samples))
    return buffer.getvalue()


def _decode_wav(payload: bytes) -> tuple[list[float], int]:
    """Extrae muestras flotantes y tasa de muestreo de un WAV PCM."""
    with wave.open(io.BytesIO(payload), "rb") as handle:
        rate = handle.getframerate()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width == 2:
        return bytes_to_float(raw), rate
    if width == 4:  # float32
        data = array.array("f")
        data.frombytes(raw)
        return list(data), rate
    if width == 1:  # uint8
        return [(value - 128) / 128.0 for value in raw], rate
    raise ValueError(f"grosor de muestra no soportado: {width}")


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #


@dataclass
class VoiceProfile:
    """Ajustes de la voz hablada."""

    backend: str = "auto"
    voice: str = ""
    language: str = "es"
    speed: float = 1.0
    model_dir: Path | None = None


class TextToSpeech:
    """Síntesis de voz con envolvente de amplitud para sincronizar la boca.

    Orden de búsqueda de backend: ``piper`` (rápido, offline, español nativo),
    ``kokoro`` (si está instalado), y por último el sintetizador del sistema
    (SAPI en Windows, ``espeak-ng``/``say`` en otros). Cada backend produce o
    reproduce PCM; cuando es posible se extrae la envolvente y se notifica a
    ``on_level`` a ~60 Hz, que es lo que anima el vértice de la boca del
    personaje.
    """

    def __init__(self, profile: VoiceProfile | None = None, logger: logging.Logger | None = None, sample_rate: int = 22050) -> None:
        self.profile = profile or VoiceProfile()
        self.log = logger or log
        self.sample_rate = sample_rate
        self._piper = None
        self._kokoro = None
        self._tried: set[str] = set()
        self._player = None
        self._stop = threading.Event()
        self._speaking = threading.Event()
        self.speaking_s = 0.0

    # ------------------------------------------------------------- backends --
    def _try_piper(self) -> bool:
        if "piper" in self._tried:
            return self._piper is not None
        self._tried.add("piper")
        try:
            from piper import PiperVoice  # type: ignore

            voice_path = self._piper_voice_path()
            if voice_path is None:
                self.log.info("piper instalado pero falta la voz .onnx (ver install.bat)")
                return False
            self._piper = PiperVoice.load(str(voice_path), config_path=None)
            self.log.info("TTS: piper con %s", voice_path.name)
            return True
        except Exception as exc:
            self.log.info("TTS piper no disponible: %s", exc)
            return False

    def _piper_voice_path(self) -> Path | None:
        try:
            import config

            directory = Path(config.TTS_VOICE_DIR)
            wanted = config.TTS_VOICE
        except Exception:
            directory = Path("assets/voices")
            wanted = ""
        candidates: list[Path] = []
        if directory.exists():
            candidates = sorted(directory.glob("*.onnx"))
            if wanted:
                preferred = [path for path in candidates if wanted in path.name]
                if preferred:
                    return preferred[0]
        env_dir = Path.home() / ".local" / "share" / "piper"
        if env_dir.exists():
            found = sorted(env_dir.glob("*.onnx"))
            if found:
                return found[0]
        return candidates[0] if candidates else None

    def _try_kokoro(self) -> bool:
        if "kokoro" in self._tried:
            return self._kokoro is not None
        self._tried.add("kokoro")
        try:
            from kokoro import KPipeline  # type: ignore

            self._kokoro = KPipeline(lang_code="a")
            self.log.info("TTS: kokoro-82M")
            return True
        except Exception as exc:
            self.log.info("TTS kokoro no disponible: %s", exc)
            return False

    def _system_backend(self) -> str:
        """Primer sintetizador del sistema que existe (puede devolver "")."""
        if sys.platform == "win32" and shutil.which("powershell"):
            return "sapi"
        for binary in ("espeak-ng", "espeak", "say"):
            if shutil.which(binary):
                return binary
        return ""

    def available(self) -> bool:
        return bool(self._try_piper() or self._try_kokoro() or self._system_backend())

    def backend_name(self) -> str:
        for name, ready in (("piper", self._piper is not None or self._try_piper()), ("kokoro", self._kokoro is not None or self._try_kokoro())):
            if ready:
                return name
        return self._system_backend() or "silencio"

    # ---------------------------------------------------------------- hablar --
    def speak(
        self,
        text: str,
        on_level: Callable[[float], None] | None = None,
        on_done: Callable[[bool], None] | None = None,
        interrupt: threading.Event | None = None,
    ) -> bool:
        """Sintetiza y reproduce. Devuelve ``False`` si se interrumpió o falló."""
        text = (text or "").strip()
        if not text:
            if on_done:
                on_done(False)
            return False
        self._stop.clear()
        self._speaking.set()
        started = time.perf_counter()
        ok = False
        try:
            for attempt in ("piper", "kokoro", "system"):
                if interrupt is not None and interrupt.is_set():
                    return False
                handler = {"piper": self._speak_piper, "kokoro": self._speak_kokoro, "system": self._speak_system}[attempt]
                if attempt == "piper" and not self._try_piper():
                    continue
                if attempt == "kokoro" and not (self._piper is None and self._try_kokoro()):
                    continue
                try:
                    ok = handler(text, on_level, interrupt)
                except Exception as exc:
                    self.log.warning("TTS %s falló: %s", attempt, exc)
                    ok = False
                if ok:
                    break
            else:
                self.log.info("sin backend de voz: la respuesta se muestra escrita")
                ok = False
        finally:
            self.speaking_s = time.perf_counter() - started
            self._speaking.clear()
            if on_done:
                try:
                    on_done(bool(ok))
                except Exception:  # pragma: no cover
                    pass
        return ok

    def speak_async(self, text: str, on_level: Callable[[float], None] | None = None, on_done: Callable[[bool], None] | None = None) -> threading.Thread:
        thread = threading.Thread(
            target=self.speak, args=(text, on_level, on_done, self._stop), name="eon-tts", daemon=True
        )
        thread.start()
        return thread

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def stop(self, drain_ms: int = 20) -> None:
        """Corte inmediato: lo invoca el barge-in (objetivo <50 ms, spec 4.2)."""
        self._stop.set()
        player = self._player
        if player is not None:
            try:
                player.abort() if hasattr(player, "abort") else player.stop()
            except Exception:  # pragma: no cover
                pass
        time.sleep(drain_ms / 1000.0)

    def _interrupted(self, interrupt: threading.Event | None) -> bool:
        return self._stop.is_set() or (interrupt is not None and interrupt.is_set())

    # ------------------------------------------------------------ por backend --
    def _speak_piper(self, text: str, on_level, interrupt) -> bool:
        voice = self._piper
        if voice is None:
            return False
        chunks: list[bytes] = []
        for packet in voice.synthesize(text, length_scale=1.0 / max(0.5, self.profile.speed)):
            sample_rate = int(getattr(packet, "sample_rate", self.sample_rate) or self.sample_rate)
            chunks.append(packet.audio_int16_bytes)
        if not chunks:
            return False
        return self._play(b"".join(chunks), sample_rate, on_level, interrupt)

    def _speak_kokoro(self, text: str, on_level, interrupt) -> bool:
        pipeline = self._kokoro
        if pipeline is None:
            return False
        pieces: list[bytes] = []
        rate = 24000
        for _grapheme, _phoneme, audio in pipeline(text, speed=self.profile.speed):
            if audio is None:
                continue
            try:
                import numpy as np  # type: ignore

                samples = (np.asarray(audio, dtype="float32") * _INT16_MAX).astype("int16")
                pieces.append(samples.tobytes())
            except Exception:  # pragma: no cover
                continue
        if not pieces:
            return False
        return self._play(b"".join(pieces), rate, on_level, interrupt)

    def _speak_system(self, text: str, on_level, interrupt) -> bool:
        backend = self._system_backend()
        if not backend:
            return False
        safe = text.replace('"', "'").replace("\n", " ")
        if backend == "sapi":  # pragma: no cover - sólo Windows
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Rate = {int(3 * (self.profile.speed - 1.0))}; "
                f'$s.Speak("{safe}")'
            )
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                    check=False, timeout=90, creationflags=_hidden_flags(),
                )
                return True
            except Exception as exc:
                self.log.debug("SAPI falló: %s", exc)
                return False
        cmd = {
            "espeak-ng": ["espeak-ng", "-v", "es", "-s", str(int(165 * self.profile.speed)), safe],
            "espeak": ["espeak", "-v", "es", "-s", str(int(165 * self.profile.speed)), safe],
            "say": ["say", "-r", str(int(175 * self.profile.speed)), safe],
        }[backend]
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while process.poll() is None:
                if self._interrupted(interrupt):
                    process.terminate()
                    return False
                time.sleep(0.05)
            return True
        except Exception as exc:
            self.log.debug("%s falló: %s", backend, exc)
            return False

    # --------------------------------------------------------------- salida --
    def _play(self, pcm: bytes, sample_rate: int, on_level, interrupt) -> bool:
        """Reproduce PCM s16le y notifica el nivel de la envolvente frame a frame."""
        player = _StreamPlayer(sample_rate=sample_rate, logger=self.log)
        self._player = player
        try:
            if not player.open():
                return self._play_via_tempfile(pcm, sample_rate, interrupt)
            frame = max(160, int(sample_rate * 0.02))
            samples = bytes_to_float(pcm)
            offset = 0
            while offset < len(samples):
                if self._interrupted(interrupt):
                    player.abort()
                    return False
                chunk = samples[offset : offset + frame]
                payload = float_to_bytes(chunk)
                player.write(payload)
                if on_level is not None:
                    try:
                        on_level(min(1.0, rms(chunk) * 3.4))
                    except Exception:  # la GUI nunca puede romper el audio
                        pass
                offset += frame
            player.drain()
            return True
        finally:
            player.close()
            self._player = None

    def _play_via_tempfile(self, pcm: bytes, sample_rate: int, interrupt) -> bool:
        """Último recurso: escribir un WAV temporal y dejarlo al reproductor del SO."""
        import tempfile

        path = Path(tempfile.gettempdir()) / f"eon-tts-{int(time.time() * 1000)}.wav"
        try:
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(pcm)
            player = _system_player(self.log)
            if not player:
                return False
            process = subprocess.Popen([*player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while process.poll() is None:
                if self._interrupted(interrupt):
                    process.terminate()
                    return False
                time.sleep(0.05)
            return True
        except Exception as exc:
            self.log.debug("no se pudo reproducir por archivo temporal: %s", exc)
            return False
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _system_player(logger: logging.Logger) -> list[str] | None:
    for binary in ("ffplay", "mpv", "paplay", "aplay", "powershell"):
        if shutil.which(binary):
            if binary == "ffplay":
                return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
            if binary == "mpv":
                return ["mpv", "--no-video"]
            if binary == "paplay":
                return ["paplay"]
            if binary == "aplay":
                return ["aplay", "-q"]
            return ["powershell", "-NoProfile", "-Command", "(New-Object Media.SoundPlayer '{0}').PlaySync()"]
    logger.debug("no hay reproductor de WAV en el sistema")
    return None


def _hidden_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


class _StreamPlayer:
    """Salida de audio de baja latencia con corte inmediato."""

    def __init__(self, sample_rate: int, logger: logging.Logger | None = None) -> None:
        self.sample_rate = int(sample_rate)
        self.log = logger or log
        self._stream = None
        self._sd = None
        try:
            import sounddevice as sd  # type: ignore

            self._sd = sd
        except Exception as exc:
            self.log.debug("sounddevice no disponible para salida: %s", exc)

    def open(self) -> bool:
        if self._sd is None:
            return False
        try:
            self._stream = self._sd.RawOutputStream(samplerate=self.sample_rate, channels=1, dtype="int16")
            self._stream.start()
            return True
        except Exception as exc:
            self.log.debug("no se pudo abrir la salida de audio: %s", exc)
            self._stream = None
            return False

    def write(self, payload: bytes) -> None:
        if self._stream is None:
            return
        self._stream.write(payload)

    def abort(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.abort()
        except Exception:  # pragma: no cover
            pass

    def drain(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
        except Exception:  # pragma: no cover
            pass

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.close()
        except Exception:  # pragma: no cover
            pass


# --------------------------------------------------------------------------- #
# Fachada de conversación
# --------------------------------------------------------------------------- #


@dataclass
class VoiceEngine:
    """Une micro, wake word, STT, TTS y barge-in en un único objeto de trabajo.

    ``main.py`` sólo necesita esto::

        voice = VoiceEngine(bus=bus, killswitch=switch)
        voice.start()
        voice.on_heard = lambda text: brain.handle(text)
    """

    bus: Any | None = None
    killswitch: Any | None = None
    logger: logging.Logger | None = None
    mic: MicStream = field(default_factory=MicStream)
    wake: WakeWordDetector = field(default_factory=WakeWordDetector)
    stt: SpeechToText = field(default_factory=SpeechToText)
    tts: TextToSpeech = field(default_factory=TextToSpeech)
    on_heard: Callable[[str], None] | None = None
    on_state: Callable[[str, dict], None] | None = None

    def __post_init__(self) -> None:
        self.log = self.logger or log
        self._barge = None
        self._capturing = threading.Event()
        self._listen_until: float = 0.0
        self._frames: deque[bytes] = deque(maxlen=int(self.mic.sample_rate * 20 / self.mic.frame_samples))
        self._lock = threading.RLock()
        self._running = False
        self._collector = None
        self.last_transcript = ""

    # ----------------------------------------------------------------- ciclo --
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.mic.subscribe("voice", self._on_frame)
        self.mic.start()
        self.stt.warmup()
        try:
            from core.barge_in import BargeInMonitor

            self._barge = BargeInMonitor(mic=self.mic, logger=self.log, on_interrupt=self._on_interrupt)
            self._barge.start()
        except Exception as exc:  # el barge-in es opcional por diseño
            self.log.info("barge-in no activo: %s", exc)
        threading.Thread(target=self._loop, name="eon-voice-loop", daemon=True).start()
        self._emit("ready", {"wake": self.wake.ready, "stt": self.stt.available(), "tts": self.tts.backend_name()})

    def stop(self) -> None:
        self._running = False
        if self._barge is not None:
            self._barge.stop()
        self.mic.stop()
        self.tts.stop()

    def _on_frame(self, pcm: bytes, sample_rate: int) -> None:
        with self._lock:
            self._frames.append(pcm)
        if not self._capturing.is_set():
            event = self.wake.feed(pcm, sample_rate)
            if event is not None:
                self.begin_listening(source="wake-word")
                self._emit("wake", {"score": event.score, "model": event.model})

    def _loop(self) -> None:
        while self._running and not (self.killswitch is not None and self.killswitch.should_stop()):
            if self._capturing.is_set() and time.monotonic() > self._listen_until:
                self.finish_listening()
            time.sleep(0.05)

    # ------------------------------------------------------------ escuchar --
    def begin_listening(self, seconds: float | None = None, source: str = "manual") -> bool:
        """Abre la ventana de captura (la dispara la wake word, la palmada o el clic)."""
        if self._capturing.is_set():
            return False
        if self.killswitch is not None and self.killswitch.engaged:
            self.log.info("escucha ignorada: kill switch activado")
            return False
        if not self.mic.available():
            self._emit("no-microphone", {"source": source})
            return False
        self._capturing.set()
        try:
            import config

            window = seconds or 6.0
            self._hard_limit = float(config.STT_MAX_SECONDS)
        except Exception:
            window, self._hard_limit = 6.0, 15.0
        self._listen_until = time.monotonic() + window
        self._speech_start: float | None = None
        self._silence_since: float | None = None
        self._emit("listening", {"source": source, "window_s": window})
        threading.Thread(target=self._watch_capture, name="eon-capture", daemon=True).start()
        return True

    def _watch_capture(self) -> None:
        """Detecta el fin de la frase por colas de silencio (hangover)."""
        try:
            import config

            hangover = float(config.BARGE_CONFIG.get("hangover_ms", 400)) / 1000.0
        except Exception:
            hangover = 0.4
        while self._capturing.is_set():
            level = self.mic.level_envelope(0.1)
            now = time.monotonic()
            if level > 0.05:
                self._speech_start = self._speech_start or now
                self._silence_since = None
            elif self._speech_start is not None:
                self._silence_since = self._silence_since or now
                if now - self._silence_since > hangover:
                    break
            elif now > self._listen_until:
                break
            time.sleep(0.03)
        if self._capturing.is_set():
            self.finish_listening()

    def finish_listening(self) -> Transcript:
        """Cierra la captura, transcribe y entrega el texto a ``on_heard``."""
        self._capturing.clear()
        samples = self.mic.recent(6.0)
        self._emit("processing", {})
        transcript = self.stt.transcribe(trim_silence(samples), self.mic.sample_rate)
        self.last_transcript = transcript.text
        self._emit("transcript", {"text": transcript.text, "backend": transcript.backend})
        if transcript.text and self.on_heard is not None:
            try:
                self.on_heard(transcript.text)
            except Exception as exc:
                self.log.exception("on_heard falló: %s", exc)
        return transcript

    @property
    def listening(self) -> bool:
        return self._capturing.is_set()

    # --------------------------------------------------------------- hablar --
    def say(self, text: str, interruptible: bool = True) -> bool:
        """Responde en voz alta con el notch en modo "hablando"."""
        text = (text or "").strip()
        if not text:
            return False
        self._emit("speaking", {"text": text[:160]})
        if self.killswitch is not None and self.killswitch.engaged:
            self._emit("silent", {"reason": "kill switch"})
            return False
        ok = self.tts.speak(text, on_level=self._on_level, on_done=lambda done: self._emit("silent", {"ok": done}))
        return ok

    def _on_level(self, level: float) -> None:
        """Cada bloque reproducido mueve la boca del personaje y las barras."""
        self._emit("amplitude", {"level": level})

    def interrupt(self) -> bool:
        """Corte manual (lo usa el barge-in y el kill switch)."""
        was = self.tts.speaking
        self.tts.stop()
        if was:
            self._emit("interrupted", {})
        return was

    # ------------------------------------------------------------------ varios --
    def capabilities(self) -> dict[str, Any]:
        return {
            "microphone": self.mic.available() and self.mic.running,
            "wake_word": self.wake.ready,
            "stt": self.stt.available(),
            "tts": self.tts.backend_name(),
            "barge_in": bool(self._barge and self._barge.active),
        }

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_state is not None:
            try:
                self.on_state(name, payload)
            except Exception as exc:
                self.log.debug("on_state falló: %s", exc)
        if self.bus is not None:
            try:
                self.bus.publish(f"eon.voice.{name}", **payload)
            except Exception:  # pragma: no cover
                pass

    def _on_interrupt(self) -> None:
        """Devuelve el micrófono al usuario mientras EON está hablando."""
        self.tts.stop()
        self.begin_listening(source="barge-in")
