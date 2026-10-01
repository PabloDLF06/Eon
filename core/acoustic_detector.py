"""Oído acústico de EON: doble palmada, saludo horario y rutina de música (spec 4.1).

Detección de palmada doble
-------------------------
Una palmada es un transitorio broadband de 5-30 ms con caída rápida. El detector
no usa un umbral fijo (eso falla en cuanto cambia la sala): mantiene un suelo de
ruido adaptativo con mediana + MAD sobre las últimas ~2 s y exige que el pulso
supere ``energy_z`` desviaciones, que decaiga antes de ``max_plateau_s`` (para
rechazar gritos, aspiradoras o música) y que el par caiga dentro de la ventana
de la spec::

    200 ms <= Δt <= 750 ms

Todo el DSP está en funciones puras que operan sobre listas de flotantes, así
que se puede verificar en un test sin micrófono.

Rutina
------
Tras reconocer la palmada: saludo según la hora local en español y arranque de
música (Spotify primero, navegador Comet como plan B, y por último el navegador
por defecto). Cada paso devuelve un resultado tipado para que la GUI pueda
contar qué pasó en vez de adivinarlo.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger("eon.acoustic")

#: Ventana "fresca" del búfer: una palmada debe caer en el último tramo de audio
#: para disparar la rutina (evita re-disparar sobre el eco de la detección previa).
RECENT_WINDOW_S = 0.85


# --------------------------------------------------------------------------- #
# Ajustes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClapConfig:
    """Parámetros del detector. Los valores por defecto vienen de ``config.CLAP_CONFIG``."""

    frame_samples: int = 256
    energy_z: float = 6.5
    abs_floor: float = 0.035
    min_gap_s: float = 0.20
    max_gap_s: float = 0.75
    window_s: float = 1.6
    refractory_s: float = 2.0
    flux_ratio: float = 0.55
    max_plateau_s: float = 0.30
    min_energy_ratio: float = 0.30   # la segunda palmada debe parecerse a la primera
    max_energy_ratio: float = 3.4

    @classmethod
    def from_config(cls) -> ClapConfig:
        try:
            import config

            raw = dict(getattr(config, "CLAP_CONFIG", {}))
            frame = max(128, int(config.SAMPLE_RATE * 0.016))
            return cls(
                frame_samples=frame,
                energy_z=float(raw.get("energy_z", 6.5)),
                abs_floor=float(raw.get("abs_floor", 0.035)),
                min_gap_s=float(raw.get("min_gap_s", 0.20)),
                max_gap_s=float(raw.get("max_gap_s", 0.75)),
                window_s=float(raw.get("window_s", 1.6)),
                refractory_s=float(raw.get("refractory_s", 2.0)),
                flux_ratio=float(raw.get("flux_ratio", 0.55)),
                max_plateau_s=float(raw.get("max_plateau_s", 0.30)),
            )
        except Exception:  # pragma: no cover - config está siempre disponible
            return cls(frame_samples=256)


@dataclass(frozen=True)
class Onset:
    """Un pico de energía candidatos a palmada."""

    time_s: float
    energy: float
    brightness: float
    rise: float

    def score(self) -> float:
        """Cuanto más agudo y más seco, más parece palmada."""
        return self.energy * (0.45 + self.brightness) * (1.0 + min(1.5, self.rise))


# --------------------------------------------------------------------------- #
# DSP puro
# --------------------------------------------------------------------------- #


HIGHPASS_CUTOFF_HZ = 1500.0
DEFAULT_SAMPLE_RATE = 16000


def frame_stats(
    samples: Sequence[float],
    frame_samples: int = 256,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> tuple[list[float], list[float]]:
    """Energía total y energía de banda aguda por frame (paso alto de un polo).

    ``highpass`` aísla el chirrido de la palmada; la voz humana tiene mucha
    menos energía relativa en esa banda, así que la relación ``bright/energy``
    separa "¡clap!" de "¡Eon!" incluso a volumen similar.
    """
    if not samples:
        return [], []
    size = max(64, int(frame_samples))
    # Paso alto de un polo con corte a 1.5 kHz: alpha = 1 / (1 + 2*pi*fc/fs).
    # Ojo: el coeficiente depende de la *frecuencia de muestreo*, no del tamaño
    # de la ventana; usar el tamaño de frame aquí atenúa la banda aguda hasta
    # hacerla cero y la detección de palmadas deja de disparar.
    alpha = 1.0 / (1.0 + 2.0 * math.pi * HIGHPASS_CUTOFF_HZ / max(8000, int(sample_rate)))
    energies: list[float] = []
    brights: list[float] = []
    hp = 0.0
    previous = 0.0
    for start in range(0, len(samples) - 1, size):
        chunk = samples[start : start + size]
        if len(chunk) < size // 2:
            break
        total = 0.0
        high = 0.0
        for value in chunk:
            total += value * value
            hp = alpha * (hp + value - previous)
            previous = value
            high += hp * hp
        energies.append(total / len(chunk))
        brights.append(high / len(chunk))
    return energies, brights


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def median_absolute_deviation(values: Sequence[float], centre: float | None = None) -> float:
    if not values:
        return 0.0
    pivot = median(values) if centre is None else centre
    return median([abs(value - pivot) for value in values]) or 1e-9


def detect_onsets(
    samples: Sequence[float],
    sample_rate: int = 16000,
    config_obj: ClapConfig | None = None,
) -> list[Onset]:
    """Lista de onsets candidatos a palmada, en orden temporal."""
    cfg = config_obj or ClapConfig.from_config()
    rate = max(8000, int(sample_rate))
    energies, brights = frame_stats(samples, cfg.frame_samples, rate)
    if len(energies) < 4:
        return []
    frame_seconds = cfg.frame_samples / rate
    history: deque[float] = deque(maxlen=max(20, int(cfg.window_s / max(1e-3, frame_seconds))))
    bright_history: deque[float] = deque(maxlen=max(20, int(cfg.window_s / max(1e-3, frame_seconds))))
    onsets: list[Onset] = []
    above_since: float | None = None
    blocked_until = 0.0
    previous_energy = 0.0
    for index, energy in enumerate(energies):
        moment = index * frame_seconds
        history.append(energy)
        brights_value = brights[index] if index < len(brights) else 0.0
        bright_history.append(brights_value)
        floor = median(history)
        spread = median_absolute_deviation(history, floor)
        threshold = max(cfg.abs_floor**2 * 0.35, floor + cfg.energy_z * spread)
        brightness = brights_value / max(1e-12, energy)
        rise = (energy - previous_energy) / max(1e-12, previous_energy + energy) if previous_energy else 0.0
        peak = energy > threshold and brightness >= cfg.flux_ratio and rise > 0.05
        if peak:
            if above_since is None and moment >= blocked_until:
                onsets.append(Onset(time_s=moment, energy=energy, brightness=brightness, rise=rise))
                blocked_until = moment + 0.06  # sin dobles detecciones de la misma palmada
            above_since = above_since if above_since is not None else moment
        else:
            if above_since is not None and moment - above_since > cfg.max_plateau_s:
                # ruido continuo (grifo, ventilador, música): se sube el suelo
                floor = max(floor, energy * 0.6)
                history.clear()
                history.extend([floor] * 8)
            above_since = None
        previous_energy = energy
    return onsets


def find_double_clap(onsets: Sequence[Onset], config_obj: ClapConfig | None = None) -> tuple[Onset, Onset, float] | None:
    """Busca el par ``200 ms <= Δt <= 750 ms`` con energías comparables."""
    cfg = config_obj or ClapConfig.from_config()
    best: tuple[Onset, Onset, float] | None = None
    for index, first in enumerate(onsets):
        for second in onsets[index + 1 :]:
            gap = second.time_s - first.time_s
            if gap < cfg.min_gap_s:
                continue
            if gap > cfg.max_gap_s:
                break
            ratio = second.energy / max(1e-12, first.energy)
            if not (cfg.min_energy_ratio <= ratio <= cfg.max_energy_ratio):
                continue
            score = first.score() + second.score()
            if best is None or score > best[2]:
                best = (first, second, score)
    return best


def analyse(samples: Sequence[float], sample_rate: int = 16000, config_obj: ClapConfig | None = None) -> tuple[list[Onset], tuple[Onset, Onset, float] | None]:
    """API de un disparo usada por los tests: onsets + par de palmadas."""
    onsets = detect_onsets(samples, sample_rate, config_obj)
    return onsets, find_double_clap(onsets, config_obj)


def make_clap_signal(sample_rate: int = 16000, gap_s: float = 0.36, width_ms: float = 12.0, amplitude: float = 0.85, noise: float = 0.004) -> list[float]:
    """Genera el audio sintético de una doble palmada (para tests y calibración)."""
    total = int(sample_rate * (gap_s + 0.6))
    samples = [noise * math.sin(2.0 * math.pi * 120.0 * i / sample_rate) for i in range(total)]
    width = max(8, int(sample_rate * width_ms / 1000.0))
    for start_s in (0.12, 0.12 + gap_s):
        start = int(start_s * sample_rate)
        for i in range(width):
            index = start + i
            if index >= total:
                break
            decay = math.exp(-4.5 * i / width)
            # chasquido broadband: ruido multiplicado por la envolvente
            value = amplitude * decay * math.sin(2.0 * math.pi * 2400.0 * i / sample_rate + i * 0.9)
            value += 0.45 * amplitude * decay * math.sin(2.0 * math.pi * 6100.0 * i / sample_rate)
            samples[index] = max(-1.0, min(1.0, value))
    return samples


# --------------------------------------------------------------------------- #
# Saludo horario (spec 4.1)
# --------------------------------------------------------------------------- #


def greeting_for(moment: datetime | None = None, name: str | None = None, windows: Sequence[tuple[int, int, str]] | None = None, fallback: str | None = None) -> str:
    """``Buenos días`` / ``Buenas tardes`` / ``Buenas noches`` + nombre.

    La franja nocturna cruza la medianoche (20:00-05:59), por eso se recorren
    todas las ventanas en lugar de calcular con resta de horas.
    """
    if windows is None or fallback is None:
        try:
            import config

            windows = windows if windows is not None else config.GREETING_WINDOWS
            fallback = fallback if fallback is not None else config.GREETING_FALLBACK
            name = name if name is not None else config.USER_NAME
        except Exception:  # pragma: no cover
            windows = windows if windows is not None else ((6, 11, "Buenos días {name}"), (12, 19, "Buenas tardes {name}"), (20, 23, "Buenas noches {name}"), (0, 5, "Buenas noches {name}"))
            fallback = fallback if fallback is not None else "Hola {name}"
            name = name if name is not None else "Pablo"
    hour = (moment or datetime.now()).hour
    for start, end, template in windows:
        if start <= end and start <= hour <= end:
            return template.format(name=name or "Pablo")
        if start > end and (hour >= start or hour <= end):
            return template.format(name=name or "Pablo")
    return (fallback or "Hola {name}").format(name=name or "Pablo")


# --------------------------------------------------------------------------- #
# Lanzador de música: Spotify -> Comet -> navegador por defecto
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LaunchResult:
    """Resultado de un intento de reproducción."""

    ok: bool
    source: str
    detail: str = ""
    command: tuple[str, ...] = ()

    def __bool__(self) -> bool:  # `if result:` hace lo correcto en toda la app
        return self.ok


@dataclass
class LauncherPaths:
    """Rutas resueltas del navegador/Spotify (útiles en el log y en tests)."""

    spotify: Path | None = None
    comet: Path | None = None
    default_browser: Path | None = None

    def describe(self) -> str:
        parts = []
        for label, value in (("spotify", self.spotify), ("comet", self.comet), ("browser", self.default_browser)):
            parts.append(f"{label}={value.name if value else '-'}")
        return " ".join(parts)


def spotify_uri(track_id: str) -> str:
    """``spotify:track:<id>``; el formato canónico de la URI de protocolo."""
    cleaned = (track_id or "").strip()
    if cleaned.startswith("spotify:"):
        return cleaned
    if "open.spotify.com/track/" in cleaned:
        tail = cleaned.split("track/", 1)[1].split("?", 1)[0]
        return f"spotify:track:{tail}"
    return f"spotify:track:{cleaned}"


def youtube_url(query: str, video_id: str = "") -> str:
    """Enlace de reproducción directa o, si no hay ID, búsqueda en YouTube.

    Usar la búsqueda en vez de un ``watch?v=`` fijo evita enlazar un vídeo
    equivocado (los ID cambian y se pueden retirar).
    """
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id.strip()}"
    from urllib.parse import quote_plus

    return f"https://www.youtube.com/results?search_query={quote_plus(query or '')}"


def _candidate_paths() -> list[Path]:
    """Rutas típicas donde vive el navegador Comet en Windows."""
    raw = [
        r"%LOCALAPPDATA%\Programs\Comet\Application\comet.exe",
        r"%LOCALAPPDATA%\Comet\Application\comet.exe",
        r"%LOCALAPPDATA%\Programs\comet\comet.exe",
        r"%LOCALAPPDATA%\comet\app\comet.exe",
        r"%PROGRAMFILES%\Comet\comet.exe",
        r"%PROGRAMFILES(X86)%\Comet\comet.exe",
        r"%LOCALAPPDATA%\Programs\Comet\Versions\comet.exe",
    ]
    out: list[Path] = []
    for template in raw:
        try:
            expanded = Path(os.path.expandvars(template))
        except Exception:  # pragma: no cover
            continue
        out.append(expanded)
    return out


def find_comet(env: dict[str, str] | None = None, probe: Callable[[Path], bool] | None = None) -> Path | None:
    """Localiza el ejecutable de Comet (navegador preferido de Pablo).

    Orden: variable ``EON_COMET`` -> ``PATH`` -> rutas típicas de instalación ->
    registro de Windows (``App Paths``). Cualquier fallo devuelve ``None``: el
    lanzador pasará al navegador por defecto en vez de romper la rutina.
    """
    environment = env if env is not None else dict(os.environ)
    exists = probe or (lambda path: path.exists())
    override = environment.get("EON_COMET") or environment.get("COMET_PATH")
    if override:
        path = Path(override)
        if exists(path):
            return path
    found = shutil.which("comet", path=environment.get("PATH")) or shutil.which("Comet", path=environment.get("PATH"))
    if found:
        return Path(found)
    for candidate in _candidate_paths():
        if exists(candidate):
            return candidate
    registry = _read_app_path("comet.exe", environment)
    return Path(registry) if registry and exists(Path(registry)) else None


def find_spotify(environment: dict[str, str] | None = None) -> Path | None:
    """Ejecutable de Spotify (instalación de usuario o del Microsoft Store)."""
    environment = environment or dict(os.environ)
    local = environment.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    candidates = [
        Path(local) / "Spotify" / "Data" / "Spotify.exe",
        Path(environment.get("PROGRAMFILES", r"C:\Program Files")) / "Spotify" / "Spotify.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    store = Path(local) / "Packages"
    if store.exists():
        try:
            for folder in store.glob("SpotifyAB.SpotifyMusic_*"):
                exe = folder / "LocalState" / "Spotify" / "Spotify.exe"
                if exe.exists():
                    return exe
        except OSError:  # pragma: no cover
            return None
    return None


def _read_app_path(exe_name: str, environment: dict[str, str]) -> str | None:
    """Consulta ``HKLM\\...\\App Paths\\<exe>`` en Windows; ``None`` si no aplica."""
    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - sólo Windows
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for suffix in (r"\Software\Microsoft\Windows\CurrentVersion\App Paths", r"\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"):
                try:
                    key = winreg.OpenKey(hive, f"{suffix}\\{exe_name}")
                except OSError:
                    continue
                try:
                    value, _ = winreg.QueryValueEx(key, "")
                except OSError:
                    value = None
                finally:
                    winreg.CloseKey(key)
                if value:
                    return str(value)
    except Exception as exc:
        log.debug("consulta de registro falló: %s", exc)
    return None


def default_browser() -> Path | None:
    """Navegador por defecto del sistema (``webbrowser`` se encarga de encontrarlo)."""
    try:
        import webbrowser

        controller = webbrowser.get()
        name = getattr(controller, "name", "") or ""
        if name:
            resolved = shutil.which(name)
            if resolved:
                return Path(resolved)
            binary = getattr(controller, "_name", None)
            if isinstance(binary, str) and binary:
                found = shutil.which(binary)
                if found:
                    return Path(found)
    except Exception as exc:
        log.debug("no se pudo resolver el navegador por defecto: %s", exc)
    return None


def process_running(pattern: str) -> bool:
    """¿Hay un proceso cuyo nombre contenga ``pattern``? (tasklist / ps)."""
    pattern = (pattern or "").lower()
    if not pattern:
        return False
    try:
        if sys.platform == "win32":
            output = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=4.0, check=False
            ).stdout
        else:  # pragma: no cover - rutas no Windows
            output = subprocess.run(
                ["ps", "-eo", "comm="], capture_output=True, text=True, timeout=4.0, check=False
            ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("no se pudo listar procesos: %s", exc)
        return False
    return any(pattern in line.lower() for line in (output or "").splitlines())


def open_with_default(url_or_uri: str) -> bool:
    """Abre un ``https://`` o una URI de protocolo con el manejador del sistema."""
    try:
        if sys.platform == "win32":  # pragma: no cover - sólo Windows
            os.startfile(url_or_uri)  # type: ignore[attr-defined]
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if not shutil.which(opener):
            import webbrowser

            return bool(webbrowser.open(url_or_uri))
        subprocess.Popen([opener, url_or_uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as exc:
        log.debug("no se pudo abrir %s: %s", url_or_uri, exc)
        return False


def browser_app_command(browser: Path | None, url: str, app_name: str = "EON") -> tuple[str, ...]:
    """Comando para abrir ``url`` como app (ventana sin pestañas, modo --app)."""
    if browser is None:
        return ()
    return (str(browser), f"--app={url}", f"--user-data-dir={Path.home() / '.eon-browser'}", "--start-maximized")


class MusicLauncher:
    """Lanza "Loser - Tame Impala" con la cascada Spotify -> Comet -> navegador.

    Cada intento es un método que devuelve :class:`LaunchResult`; así el registro
    cuenta la historia completa y la GUI puede decir «Spotify no estaba abierto,
    lo puse desde Comet» sin adivinar nada.
    """

    def __init__(self, logger: logging.Logger | None = None, runner: Callable[[Sequence[str]], bool] | None = None) -> None:
        self.log = logger or log
        self._runner = runner or self._default_runner
        try:
            import config

            self.settings = dict(config.MUSIC_CONFIG)
        except Exception:  # pragma: no cover
            self.settings = {"title": "Loser", "artist": "Tame Impala", "spotify_track_id": "", "youtube_query": "Tame Impala - Loser"}
        self.paths = LauncherPaths()
        self.last_result: LaunchResult | None = None

    # ------------------------------------------------------------------ API --
    def resolve(self) -> LauncherPaths:
        """Descubre rutas sin lanzar nada (lo pide el panel de diagnóstico)."""
        self.paths = LauncherPaths(
            spotify=find_spotify(),
            comet=find_comet(),
            default_browser=default_browser(),
        )
        return self.paths

    def status(self) -> dict[str, bool]:
        paths = self.resolve()
        return {
            "spotify_installed": paths.spotify is not None,
            "spotify_running": process_running("spotify"),
            "comet_installed": paths.comet is not None,
            "browser_installed": paths.default_browser is not None,
        }

    def play(self, wait_s: float | None = None) -> LaunchResult:
        """Reproduce la pista objetivo; nunca lanza excepciones."""
        timeout = float(wait_s if wait_s is not None else self.settings.get("launch_timeout_s", 12.0))
        attempts: list[LaunchResult] = []
        result = self._try_spotify(timeout)
        attempts.append(result)
        if result.ok:
            self.last_result = result
            return result
        result = self._try_browser_app(timeout)
        attempts.append(result)
        if result.ok:
            self.last_result = result
            return result
        self.last_result = LaunchResult(False, "ninguno", " · ".join(item.detail or "sin detalle" for item in attempts))
        return self.last_result

    def stop(self) -> LaunchResult:
        """Pausa razonable: manda la tecla de reproducción/pausa al foco."""
        if process_running("spotify"):
            ok = self._send_media_key(0xB3)  # VK_MEDIA_PLAY_PAUSE
            return LaunchResult(ok, "Spotify", "pausa enviada por tecla multimedia")
        return LaunchResult(False, "ninguno", "no hay reproductor detectado")

    # -------------------------------------------------------------- intentos --
    def _try_spotify(self, timeout: float) -> LaunchResult:
        track_id = str(self.settings.get("spotify_track_id", "")).strip()
        if not track_id:
            return LaunchResult(False, "Spotify", "sin identificador de pista")
        uri = spotify_uri(track_id)
        running = process_running("spotify")
        exe = find_spotify()
        if not running and exe is None:
            return LaunchResult(False, "Spotify", "Spotify no está instalado")
        if running:
            ok = open_with_default(uri)
            if ok and self._wait_for(lambda: process_running("spotify"), timeout=2.0):
                return LaunchResult(True, "Spotify", "reproduciendo desde el cliente abierto", (uri,))
        launched = exe is not None and self._runner([str(exe), uri])
        opened = launched or open_with_default(uri)
        if not opened:
            return LaunchResult(False, "Spotify", "el protocolo spotify: no respondió")
        if self._wait_for(lambda: process_running("spotify"), timeout=min(6.0, timeout)):
            return LaunchResult(True, "Spotify", "cliente lanzado y detectado", (str(exe) if exe else uri,))
        return LaunchResult(False, "Spotify", "Spotify no llegó a abrirse en la espera")

    def _try_browser_app(self, timeout: float) -> LaunchResult:
        url = youtube_url(str(self.settings.get("youtube_query", "")), str(self.settings.get("youtube_video_id", "")))
        browser = find_comet() or default_browser()
        label = "Comet" if browser and "comet" in browser.name.lower() else "navegador"
        if browser is None:
            if open_with_default(url):
                return LaunchResult(True, "web", f"enlace abierto con el manejador del sistema ({url})", (url,))
            return LaunchResult(False, label, "no hay navegador utilizable")
        command = browser_app_command(browser, url, str(self.settings.get("browser_app_name", "EON")))
        if not self._runner(list(command)):
            command = (str(browser), url)
            if not self._runner(list(command)):
                return LaunchResult(False, label, "el navegador rechazó el arranque", command)
        # se confirma que el proceso arrancó, pero el lanzamiento ya es válido sin
        # esa confirmación: hay navegadores que delegan en una instancia existente
        confirmed = self._wait_for(lambda: process_running(browser.stem), timeout=min(4.0, timeout))
        detail = f"{url}" + ("" if confirmed else " (sin confirmación de proceso)")
        return LaunchResult(True, label, detail, command)

    def _default_runner(self, command: Sequence[str]) -> bool:
        if not command:
            return False
        try:
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
            subprocess.Popen(
                list(command),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            return True
        except (OSError, ValueError) as exc:
            self.log.debug("no se pudo ejecutar %s: %s", command, exc)
            return False

    @staticmethod
    def _wait_for(predicate: Callable[[], bool], timeout: float, poll: float = 0.2) -> bool:
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return True
            except Exception:  # pragma: no cover - predictores exóticos
                return False
            time.sleep(poll)
        return False

    @staticmethod
    def _send_media_key(vk: int) -> bool:  # pragma: no cover - sólo Windows
        if sys.platform != "win32":
            return False
        try:
            import ctypes

            KEYEVENTF_KEYUP = 0x0002
            user32 = ctypes.windll.user32
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            return True
        except Exception as exc:
            log.debug("tecla multimedia no enviada: %s", exc)
            return False


# --------------------------------------------------------------------------- #
# Oreja: escucha continua + rutina completa
# --------------------------------------------------------------------------- #


@dataclass
class AcousticEar:
    """Consume el micrófono compartido y dispara la rutina de la palmada doble.

    ``on_trigger`` se llama con ``(saludo, LaunchResult)`` para que el orquestador
    (``main.py``) hable y abra la media card; así este módulo no importa GUI ni
    motor de voz y se puede probar entero.
    """

    mic: object | None = None
    on_trigger: Callable[[str, LaunchResult | None], None] | None = None
    logger: logging.Logger | None = None
    config_obj: ClapConfig = field(default_factory=ClapConfig.from_config)
    enabled: bool = True
    min_interval_s: float = 4.0

    def __post_init__(self) -> None:
        self.log = self.logger or log
        self._buffer: list[float] = []
        self._sample_rate = 16000
        self._last_fire = 0.0
        self._detach: Callable[[], None] | None = None
        self._lock = threading.RLock()
        self.launcher = MusicLauncher(logger=self.log)
        self.claps_seen = 0
        self.triggers = 0

    # ------------------------------------------------------------------ API --
    def start(self) -> bool:
        if self.mic is None:
            self.log.info("oído acústico sin micrófono: la palmada doble queda inactiva")
            return False
        try:
            self._sample_rate = int(getattr(self.mic, "sample_rate", 16000))
            self._detach = self.mic.subscribe("claps", self._on_frame)  # type: ignore[attr-defined]
        except Exception as exc:
            self.log.info("no se pudo suscribir el detector de palmadas: %s", exc)
            return False
        self.log.info("oído acústico activo (umbral z=%.1f, ventana %.0f-%.0f ms)", self.config_obj.energy_z, self.config_obj.min_gap_s * 1000, self.config_obj.max_gap_s * 1000)
        return True

    def stop(self) -> None:
        detach, self._detach = self._detach, None
        if callable(detach):
            try:
                detach()
            except Exception:  # pragma: no cover
                pass

    def feed(self, samples: Sequence[float], sample_rate: int | None = None) -> bool:
        """Alimentación manual; devuelve ``True`` si se disparó la rutina."""
        rate = int(sample_rate or self._sample_rate)
        keep = int(rate * (self.config_obj.window_s + 0.5))
        with self._lock:
            self._buffer.extend(samples)
            if len(self._buffer) > keep:
                del self._buffer[: len(self._buffer) - keep]
            snapshot = list(self._buffer)
        onsets, pair = analyse(snapshot, rate, self.config_obj)
        self.claps_seen = max(self.claps_seen, len(onsets))
        if pair is None:
            return False
        first, second, _score = pair
        # sólo cuenta un par *reciente*: los tiempos son relativos al inicio del
        # búfer, así que exigir que caiga en el último tramo evita re-disparos
        buffer_end = len(snapshot) / max(1, rate)
        if second.time_s < buffer_end - RECENT_WINDOW_S:
            return False
        now = time.monotonic()
        if now - self._last_fire < self.min_interval_s:
            return False
        self._last_fire = now
        self.triggers += 1
        with self._lock:
            self._buffer.clear()
        greeting = greeting_for()
        self.log.info("doble palmada detectada (Δt=%.0f ms)", (second.time_s - first.time_s) * 1000.0)
        threading.Thread(target=self._routine, args=(greeting,), name="eon-acoustic-routine", daemon=True).start()
        return True

    # -------------------------------------------------------------- interno --
    def _on_frame(self, pcm: bytes, sample_rate: int) -> None:
        if not self.enabled:
            return
        from core.voice_engine import bytes_to_float

        self.feed(bytes_to_float(pcm), sample_rate)

    def _routine(self, greeting: str) -> None:
        result: LaunchResult | None = None
        try:
            result = self.launcher.play()
        except Exception as exc:  # la rutina de música no puede tumbar la app
            self.log.warning("rutina de música falló: %s", exc)
        if self.on_trigger is not None:
            try:
                self.on_trigger(greeting, result)
            except Exception as exc:
                self.log.debug("on_trigger falló: %s", exc)


def snapshot_start_age(samples: Sequence[float], sample_rate: int) -> float:
    """Instante (en segundos) que corresponde al inicio del búfer analizado.

    Se usa para descartar pares viejos: si el buffer tiene 2 s de audio, una
    palmada detectada "a los 0.1 s" ocurrió hace casi 2 s y no debe re-disparar.
    """
    return max(0.0, len(samples) / max(1, sample_rate))
