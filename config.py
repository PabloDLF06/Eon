"""Configuración global de EON.

Este módulo es la única fuente de verdad para rutas, colores, tamaños del
Dynamic Notch, nombres de modelos de Ollama, presupuestos de VRAM y los
parámetros de seguridad. Cualquier ajuste que Pablo quiera cambiar sin tocar
código puede sobreescribirse en un archivo ``settings.json`` junto a este
módulo; las claves desconocidas se ignoran con un aviso en el log.

Reglas de diseño del módulo:

* No importa nada de ``PyQt6``, ``numpy`` ni de ningún backend opcional: debe
  poder importarse en cualquier máquina (incluida una sin entorno gráfico)
  para ejecutar los tests o el instalador.
* Todas las rutas se devuelven como :class:`pathlib.Path` absolutos.
* Los valores numéricos delicados (presupuestos de VRAM, umbrales de kill
  switch) llevan comentario explicando *por qué* vale ese número.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Rutas del proyecto
# --------------------------------------------------------------------------- #
ROOT_DIR: Path = Path(__file__).resolve().parent
ASSETS_DIR: Path = ROOT_DIR / "assets"
CHAR_ASSETS_DIR: Path = ASSETS_DIR / "char"
SOUND_ASSETS_DIR: Path = ASSETS_DIR / "sounds"
LOGS_DIR: Path = ROOT_DIR / "logs"
WORKSPACE_DIR: Path = ROOT_DIR / "workspace"
PROJECTS_DIR: Path = WORKSPACE_DIR / "projects"
CACHE_DIR: Path = WORKSPACE_DIR / "cache"
SETTINGS_FILE: Path = ROOT_DIR / "settings.json"

#: Directorios que el motor de auto-programación tiene terminantemente
#: prohibidos de escribir. Ver ``core.self_programmer`` y ``safety.killswitch``.
PROTECTED_PATHS: tuple[str, ...] = (
    "safety",
    ".git",
    "config.py",
    "install.bat",
    "start.bat",
    "requirements.txt",
)


def ensure_runtime_dirs() -> None:
    """Crea los directorios de trabajo si faltan (idempotente)."""
    for directory in (LOGS_DIR, WORKSPACE_DIR, PROJECTS_DIR, CACHE_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover - sólo en sistemas de lectura única
            pass


# --------------------------------------------------------------------------- #
# Paleta "Cyberpunk Cyan" y color del personaje
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Palette:
    """Colores base del sistema. Se serializan como ``#rrggbb`` (formato Qt)."""

    cyan: str = "#00e5ff"          # estado de escucha / acento principal
    cyan_glow: str = "#00f0ff"     # borde luminoso de pantalla (spec 2.3)
    cyan_deep: str = "#0b7d8c"     # sombra del acento
    violet: str = "#a78bfa"        # halo de "pensando" (referencia visual)
    amber: str = "#ffc46b"         # avisos / media card
    rose: str = "#ff6b8a"          # errores
    green: str = "#4ade80"         # OK / acciones completadas

    # Piel del personaje: degradado vertical marfil -> lila muy suave.
    char_top: str = "#fdf6f1"
    char_mid: str = "#f3e7e6"
    char_bottom: str = "#ddd0f0"
    char_line: str = "#1a1721"     # ojos, boca, antenas
    char_blush: str = "#f7a8bb"    # mejillas
    char_shine: str = "#ffffff"    # especular superior

    notch_shell: str = "#050508"   # cápsula negra del notch
    notch_rim: str = "#1c1b24"     # filo exterior sutil
    text: str = "#eef2f7"
    text_dim: str = "#8f93a6"

    def rgb(self, hex_color: str) -> tuple[int, int, int]:
        """Convierte ``#rrggbb`` en una tupla ``(r, g, b)``."""
        value = hex_color.lstrip("#")
        if len(value) == 3:  # notación corta #rgb
            value = "".join(ch * 2 for ch in value)
        if len(value) != 6:
            raise ValueError(f"color hexadecimal inválido: {hex_color!r}")
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


COLORS = Palette()


# --------------------------------------------------------------------------- #
# Dynamic Notch: geometrías de estado (spec 2.1)
# --------------------------------------------------------------------------- #
#: Altura total de la "franja" superior que el notch puede ocupar al animar.
NOTCH_BAND_HEIGHT: int = 150
NOTCH_TOP_INSET: int = 0            # pegado al bisel: 0 = solapado con el borde
NOTCH_CORNER_RATIO: float = 0.66    # radio inferior = altura * ratio
NOTCH_MIN_CORNER: float = 12.0
NOTCH_MAX_CORNER: float = 28.0

#: (ancho, alto) de cada estado cinético. Copiados literalmente de la spec.
NOTCH_SIZES: dict[str, tuple[int, int]] = {
    "idle": (140, 32),
    "listening": (260, 42),
    "thinking": (220, 48),
    "media": (360, 80),
    "action": (360, 80),
    "error": (300, 64),
    "sleeping": (120, 30),
}

#: Duraciones de las transiciones, en milisegundos.
NOTCH_ANIM_MS: dict[str, int] = {
    "expand": 420,      # con OutBack: ligero rebote, sensación "gelatina"
    "collapse": 300,    # OutCubic: salida seca, sin rebote
    "state_change": 220,
}

#: Barritas de audio del estado de escucha y de la media card.
SOUNDBAR_COUNT: int = 13
SOUNDBAR_MIN_HEIGHT: float = 0.12   # fracción de la altura útil
SOUNDBAR_MAX_HEIGHT: float = 1.0
SOUNDBAR_FPS: int = 60
EQUALIZER_BARS: int = 24

#: Brillo ambiental "respirando" del notch en reposo.
IDLE_GLOW_MIN_ALPHA: int = 26
IDLE_GLOW_MAX_ALPHA: int = 78
IDLE_BREATH_PERIOD_S: float = 3.0


# --------------------------------------------------------------------------- #
# Personaje vivo (spec 2.2)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CharTuning:
    """Constantes de kinemática del personaje. Ajustar aquí, no en el widget."""

    fps: int = 60
    breath_period_s: float = 3.0          # ciclo vertical de respiración
    breath_amplitude: float = 1.35        # píxeles de desplazamiento
    blink_interval_s: tuple[float, float] = (4.0, 7.0)  # parpadeo natural
    blink_close_s: float = 0.07
    blink_hold_s: float = 0.035
    blink_open_s: float = 0.11
    saccade_interval_s: tuple[float, float] = (1.1, 2.9)
    saccade_max_offset: float = 1.15      # pixeles de "dart" ocular
    saccade_dwell_s: float = 0.14
    mouth_smile: float = 0.28            # curvatura de reposo de la boca
    wake_pulse_s: float = 0.9            # duración de la onda radial al despertar
    ear_perk_ms: int = 180                # tiempo de erección de las antenas
    z_particle_count: int = 3
    z_particle_period_s: float = 2.4
    visor_scan_s: float = 1.6            # barrido del visor HUD
    body_width_ratio: float = 0.90       # ancho del cuerpo vs. ancho útil
    body_height_ratio: float = 0.82
    squircle_exponent: float = 3.2       # 2 = elipse, más alto = más "cuadrado"
    squircle_samples: int = 96


CHAR = CharTuning()


# --------------------------------------------------------------------------- #
# Glow perimetral de pantalla (spec 2.3)
# --------------------------------------------------------------------------- #
GLOW_STROKE_MIN: float = 4.0
GLOW_STROKE_MAX: float = 12.0
GLOW_FADE_IN_MS: int = 150
GLOW_FADE_OUT_MS: int = 300
GLOW_BLUR_PASSES: int = 4                 # capas de trazo para simular desenfoque
GLOW_SCAN_ENABLED: bool = True            # línea de escaneo al capturar pantalla


# --------------------------------------------------------------------------- #
# Ollama, modelos y la Ley de Oro: monogamia de VRAM
# --------------------------------------------------------------------------- #
OLLAMA_HOST: str = os.environ.get("EON_OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT_S: float = 300.0
OLLAMA_CONNECT_TIMEOUT_S: float = 3.0

MODEL_BRAIN: str = "llama3.1:8b"
MODEL_VISION: str = "llama3.2-vision:latest"
MODEL_CODER: str = "qwen2.5-coder:7b"

#: VRAM de la RTX 4060 Laptop.
GPU_VRAM_GB: float = 8.0
#: Tope duro de asignación: por debajo del total para que el driver y Windows
#: conserven margen. Si un modelo superara esto, **no** se carga: la alternativa
#: es volcar pesos a los ~3.5 GB de RAM libres y congelar el sistema (OOM).
MAX_VRAM_BUDGET_GB: float = 6.4
#: Ventana sana de consumo con el sistema en reposo (spec 1.4).
IDLE_VRAM_TARGET_GB: tuple[float, float] = (1.5, 2.5)
#: Cuánto espera el router a que Ollama confirme la descarga antes de dar por
#: perdida la operación. Sin esta espera, los pesos viejos y nuevos conviven.
UNLOAD_WAIT_S: float = 8.0
UNLOAD_POLL_INTERVAL_S: float = 0.15
#: Mantiene el cerebro cargado entre peticiones para no recargarlo cada vez.
BRAIN_KEEP_ALIVE: str = "6m"
EPHEMERAL_KEEP_ALIVE: str = "0"

#: Presuposición de peso (GB) por modelo si Ollama no reporta ``size_vram``.
MODEL_SIZE_GUESS_GB: dict[str, float] = {
    MODEL_BRAIN: 4.9,
    MODEL_VISION: 5.2,
    MODEL_CODER: 4.7,
}


# --------------------------------------------------------------------------- #
# Audio: escucha permanente, STT y TTS
# --------------------------------------------------------------------------- #
SAMPLE_RATE: int = 16_000
FRAME_MS: int = 30
#: Wake word. openWakeWord trae un modelo "hey_sir" que usamos como base
#: compatible cuando el modelo personalizado "eon" no está entrenado aún.
WAKE_WORD: str = "eon"
WAKE_MODEL_CANDIDATES: tuple[str, ...] = ("eon", "hey_eon", "hey_sir")
WAKE_THRESHOLD: float = 0.62
WAKE_REFRACTORY_S: float = 1.2

#: Detección de palmadas (spec 4.1)
CLAP_CONFIG = {
    "energy_z": 6.5,             # una palmada real es >= 6.5 desviaciones del ruido
    "abs_floor": 0.035,          # RMS mínimo: nada por debajo es susurro/ambiente
    "min_gap_s": 0.20,           # Δt mínima entre palmadas  (200 ms)
    "max_gap_s": 0.75,           # Δt máxima entre palmadas  (750 ms)
    "window_s": 1.6,             # memoria del detector
    "refractory_s": 2.0,         # no re-disparar justo después de un disparo
    "flux_ratio": 0.55,          # flujo espectral alto = transitorio, no ruido continuo
    "max_plateau_s": 0.30,       # si el exceso dura más de esto, es ruido continuo
}

#: Barge-in (spec 4.2). El corte del audio propio ocurre en el primer frame
#: confirmado; el resto del presupuesto (70 ms) se gasta en filtrar eco.
BARGE_CONFIG = {
    "aggressiveness": 2,         # 0-3 para webrtcvad
    "voiced_frames": 2,          # frames consecutivos de voz antes de cortar
    "min_speech_ms": 90,
    "tts_echo_guard": 2.6,       # umbral multiplicador mientras EON habla
    "max_cut_ms": 70.0,          # presupuesto objetivo de latencia de corte
    "hangover_ms": 400,          # silencio necesario para dar por terminada la frase
}

WHISPER_MODEL: str = "base"
WHISPER_COMPUTE: str = "int8"
#: Whisper en CPU tarda ~0.4 s en un utterance corto y deja la VRAM libre para
#: los LLM. Mantenerlo fuera de GPU es deliberado (monogamia de VRAM).
WHISPER_DEVICE: str = "cpu"
WHISPER_BEAM_SIZE: int = 1
WHISPER_LANGUAGE: str = "es"
#: Transcripción se corta tras este tiempo para evitar consumos infinitos.
STT_MAX_SECONDS: float = 15.0

#: TTS en orden de preferencia. Cada backend se activa sólo si está instalado;
#: el último (SAPI5 vía PowerShell / espeak) no necesita dependencias extra.
TTS_BACKENDS: tuple[str, ...] = ("piper", "kokoro", "system")
TTS_LANGUAGE: str = "es"
TTS_VOICE: str = "es_ES-shjelm-medium"      # Piper: voz masculina cálida en español
TTS_VOICE_DIR: Path = ASSETS_DIR / "voices"
TTS_SPEED: float = 1.0
TTS_BLOCK_MS: int = 40                      # tamaño de bloque enviado al DAC
#: Puntos de la envolvente de amplitud que se pasan al personaje para la boca.
TTS_ENVELOPE_BINS: int = 96

#: Host de audio. ``None`` = dispositivo por defecto del sistema.
INPUT_DEVICE: str | None = None
OUTPUT_DEVICE: str | None = None
MIC_ENABLED: bool = True
SPEAKER_ENABLED: bool = True


# --------------------------------------------------------------------------- #
# Rutina acústica: saludo y música (spec 4.1)
# --------------------------------------------------------------------------- #
USER_NAME: str = "Pablo"
GREETING_WINDOWS: tuple[tuple[int, int, str], ...] = (
    (6, 11, "Buenos días {name}"),
    (12, 19, "Buenas tardes {name}"),
    (20, 23, "Buenas noches {name}"),
    (0, 5, "Buenas noches {name}"),
)
GREETING_FALLBACK: str = "Hola {name}, soy Eon"

MUSIC_CONFIG = {
    "title": "Loser",
    "artist": "Tame Impala",
    "spotify_track_id": "5R33JqqJ1jYmAWKhxkdXJq",
    "youtube_query": "Tame Impala - Loser official audio",
    "browser_app_name": "Comet",
    "launch_timeout_s": 12.0,
    "fallback_sleep_s": 1.2,
}

#: Modo de confirmación antes de actuar sobre el escritorio:
#:   "off"     -> EON pregunta SIEMPRE antes de cada clic/tecleo
#:   "danger"  -> pregunta sólo en acciones destructivas (escribir, borrar, enviar)
#:   "auto"    -> ejecuta sin preguntar (kill switch sigue activo)
ACTION_CONFIRM_MODE: str = "danger"
#: Máximo de pasos por plan de automatización para que un alucinación del modelo
#: no dispare un bucle infinito de clics.
MAX_ACTION_STEPS: int = 12
#: Espacio de coordenadas que usa el modelo de visión:
#:   "auto"            -> fracciones 0..1 se escalan; el resto se toma como píxeles
#:   "normalized1000"  -> el modelo emite unidades 0..1000 (estilo Qwen2-VL)
#:   "pixels"          -> siempre píxeles de la imagen enviada
VISION_COORD_SPACE: str = "auto"
#: Monitor a capturar con ``mss``: 0 = escritorio virtual completo, 1 = primario.
VISION_MONITOR: int = 0
CLICK_DURATION_RANGE_MS: tuple[int, int] = (45, 110)
TYPE_CHAR_DELAY_S: float = 0.028
BEZIER_SEGMENTS: int = 34


# --------------------------------------------------------------------------- #
# Kill switch (spec 4.4)
# --------------------------------------------------------------------------- #
KILLSWITCH_COMBO: tuple[str, ...] = ("ctrl", "shift", "space")
#: Sacudida violenta del ratón: desplazamiento euclídeo acumulado por encima de
#: ``SHAKE_DISTANCE_PX`` dentro de ``SHAKE_WINDOW_MS`` = pánico.
SHAKE_DISTANCE_PX: float = 1500.0
SHAKE_WINDOW_MS: float = 300.0
SHAKE_SAMPLE_HZ: int = 120
#: Hash de referencia de ``safety/killswitch.py``. Se recalcula conscientemente
#: con ``python tools/attest_killswitch.py``; el motor de auto-programación
#: nunca puede actualizarlo, por eso vive también aquí y en un archivo aparte.
KILLSWITCH_SHA256: str = ""
KILLSWITCH_BASELINE_FILE: Path = ASSETS_DIR / "integrity" / "killswitch.sha256"
#: Si el hash no coincide, EON se niega a arrancar salvo que el usuario lo
#: exima explícitamente (variable de entorno) -- útil sólo para desarrollo.
ENFORCE_KILLSWITCH_INTEGRITY: bool = True
ALLOW_KILLSWITCH_OVERRIDE: str = "EON_DEV_SKIP_KILLSWITCH_HASH"


# --------------------------------------------------------------------------- #
# Self-programming y fábrica de apps
# --------------------------------------------------------------------------- #
SELF_UPGRADE_BRANCH_PREFIX: str = "feature/self-upgrade-"
PYTEST_TIMEOUT_S: float = 240.0
GIT_TIMEOUT_S: float = 60.0
#: EON fusiona en la rama local pero NUNCA sube a remoto sin permiso explícito.
AUTO_PUSH: bool = False
HOT_RELOAD_ON_MERGE: bool = True

APP_BUILDER_CONFIG = {
    "max_files": 40,
    "max_file_bytes": 120_000,
    "allowed_extensions": (".html", ".css", ".js", ".json", ".py", ".md", ".txt", ".svg", ".toml"),
    "stacks": ("web", "python", "tkinter"),
    "venv": False,                # crear venv por app es opcional y lento
    "install_deps": False,        # se instala sólo si el manifiesto lo pide y con red
    "preview_port_range": (8765, 8790),
}


# --------------------------------------------------------------------------- #
# Ventana de eventos / registro
# --------------------------------------------------------------------------- #
LOG_LEVEL: str = os.environ.get("EON_LOG_LEVEL", "INFO")
LOG_FILE: Path = LOGS_DIR / "eon.log"
MAX_BYTES: int = 2_000_000
BACKUP_COUNT: int = 3
QUIET_CONSOLE: bool = True


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Fusiona ``extra`` sobre ``base`` para claves de dict, sustituyendo el resto."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def apply_overrides(overrides: dict[str, Any]) -> list[str]:
    """Aplica un ``dict`` de ajustes sobre este módulo.

    Devuelve la lista de claves aplicadas. Las claves que no existen en el
    módulo se ignoran (con aviso) para que un error de tipeo en
    ``settings.json`` no impida arrancar EON.
    """
    applied: list[str] = []
    module = sys.modules[__name__]
    for key, value in (overrides or {}).items():
        if not hasattr(module, key):
            _logger().warning("settings.json: clave desconocida '%s' (ignorada)", key)
            continue
        if key == "COLORS" and isinstance(value, dict):
            data = _deep_merge({f.name: getattr(COLORS, f.name) for f in COLORS.__dataclass_fields__.values()}, value)  # type: ignore[attr-defined]
            module.COLORS = Palette(**data)
        elif key == "CHAR" and isinstance(value, dict):
            data = _deep_merge({f.name: getattr(CHAR, f.name) for f in CHAR.__dataclass_fields__.values()}, value)  # type: ignore[attr-defined]
            module.CHAR = CharTuning(**data)
        else:
            setattr(module, key, value)
        applied.append(key)
    return applied


def load_settings_file(path: Path | None = None) -> dict[str, Any]:
    """Lee ``settings.json`` si existe; nunca lanza excepciones."""
    target = path or SETTINGS_FILE
    try:
        if not target.exists():
            return {}
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            logging.getLogger("eon.config").warning("settings.json no es un objeto JSON")
            return {}
        return payload
    except (OSError, ValueError) as exc:
        logging.getLogger("eon.config").warning("No se pudo leer settings.json: %s", exc)
        return {}


def init_logging(level: str | None = None) -> logging.Logger:
    """Configura el logger raíz ``eon`` con salida a archivo rotativo y consola.

    Los handlers se crean una sola vez: volver a llamar es seguro.
    """
    logger = logging.getLogger("eon")
    if getattr(logger, "_eon_configured", False):
        return logger
    logger.setLevel(getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO))
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s", "%H:%M:%S"
    )
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:  # disco de sólo lectura, permisos, etc.
        pass
    if not QUIET_CONSOLE or os.environ.get("EON_VERBOSE"):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    logger._eon_configured = True  # type: ignore[attr-defined]
    return logger


def _logger() -> logging.Logger:
    """Logger perezoso: evita exigir que ``init_logging`` ya se haya llamado."""
    return init_logging()


def is_windows() -> bool:
    return os.name == "nt" and sys.platform.startswith("win")


def feature_flags() -> dict[str, bool]:
    """Resumen de capacidades para el log de arranque y para el notch."""
    return {
        "windows": is_windows(),
        "headless": not os.environ.get("DISPLAY") and not is_windows(),
        "python": sys.version_info >= (3, 10),
    }


@dataclass
class RuntimePaths:
    """Rutas efectivas una vez creados los directorios de trabajo."""

    root: Path = ROOT_DIR
    assets: Path = ASSETS_DIR
    logs: Path = LOGS_DIR
    workspace: Path = WORKSPACE_DIR
    projects: Path = PROJECTS_DIR
    cache: Path = CACHE_DIR
    extra: dict[str, Any] = field(default_factory=dict)

    def ensure(self) -> RuntimePaths:
        ensure_runtime_dirs()
        return self


def runtime_paths() -> RuntimePaths:
    """Fábrica de :class:`RuntimePaths` con los directorios ya garantizados."""
    return RuntimePaths().ensure()
