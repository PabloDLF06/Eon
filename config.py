"""Load validated local settings once; publish explicit notch edits atomically."""

from __future__ import annotations

from copy import deepcopy
import json
import math
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any


class ConfigurationError(ValueError):
    """Report missing, unreadable or invalid user settings explicitly."""


SETTINGS_PATH = Path(__file__).resolve().parent / "user_settings.json"
REQUIRED_ROLES = ("brain", "vision", "coding", "embeddings", "reasoning_auditor")
SUPPORTED_STT_MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
SUPPORTED_WAKE_WORDS = ("alexa", "hey_mycroft", "hey_jarvis", "hey_rhasspy", "timer", "weather")
logger = logging.getLogger(__name__)


def _load_settings(path: Path) -> dict[str, Any]:
    """Read a JSON object and validate every setting consumed by this module."""
    try:
        with path.open("r", encoding="utf-8-sig") as settings_file:
            settings = json.load(settings_file)
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"Falta el archivo de configuración {path}. Se espera un objeto JSON "
            "con model_assignments y los ajustes de usuario."
        ) from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ConfigurationError(
            f"El archivo {path} está corrupto. Se espera un objeto JSON válido "
            "codificado en UTF-8, sin comentarios."
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"No se puede leer {path}. Se espera un archivo JSON UTF-8 accesible."
        ) from exc

    return _validate_settings(settings, path)


def _validate_settings(settings: Any, path: Path) -> dict[str, Any]:
    """Share the exact same validation between loading and explicit saving."""
    def invalid(detail: str) -> ConfigurationError:
        """Attach the source path to a configuration validation failure."""
        return ConfigurationError(f"Configuración inválida en {path}: {detail}.")

    if not isinstance(settings, dict):
        raise invalid("se espera un objeto JSON en la raíz")
    assignments = settings.get("model_assignments")
    if not isinstance(assignments, dict):
        raise invalid("model_assignments debe ser un objeto JSON")
    missing = [role for role in REQUIRED_ROLES if role not in assignments]
    if missing:
        raise invalid(f"faltan roles obligatorios en model_assignments: {', '.join(missing)}")
    for role, assignment in assignments.items():
        if not isinstance(assignment, dict):
            raise invalid(f"model_assignments.{role} debe ser un objeto JSON")
        for field in ("provider", "model"):
            value = assignment.get(field)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise invalid(f"model_assignments.{role}.{field} debe ser un texto no vacío sin espacios externos")
    if type(settings.get("notch_auto_hide_enabled")) is not bool:
        raise invalid("notch_auto_hide_enabled debe ser true o false")
    seconds = settings.get("notch_auto_hide_seconds")
    if type(seconds) is not int or seconds <= 0:
        raise invalid("notch_auto_hide_seconds debe ser un entero positivo")
    shortcuts = settings.get("quick_launch_shortcuts")
    if not isinstance(shortcuts, list):
        raise invalid("quick_launch_shortcuts debe ser una lista")
    for index, shortcut in enumerate(shortcuts):
        prefix = f"quick_launch_shortcuts[{index}]"
        if not isinstance(shortcut, dict) or set(shortcut) != {"label", "path", "color"}:
            raise invalid(f"{prefix} debe contener exactamente label, path y color")
        for field in ("label", "path", "color"):
            value = shortcut[field]
            if (not isinstance(value, str) or not value.strip() or value != value.strip()
                    or any(ord(char) < 32 for char in value)):
                raise invalid(f"{prefix}.{field} debe ser texto no vacío sin espacios externos ni controles")
        if not Path(shortcut["path"]).is_absolute():
            raise invalid(f"{prefix}.path debe ser una ruta absoluta, sin argumentos de comando")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", shortcut["color"]):
            raise invalid(f"{prefix}.color debe tener formato #RRGGBB")
    for field in ("voice_profile", "language"):
        if not isinstance(settings.get(field), str) or not settings[field].strip():
            raise invalid(f"{field} debe ser un texto no vacío")
    limits = settings.get("cost_limits")
    if not isinstance(limits, dict):
        raise invalid("cost_limits debe ser un objeto JSON")
    for field in ("daily_usd_cap", "monthly_usd_cap"):
        value = limits.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise invalid(f"cost_limits.{field} debe ser un número finito no negativo")
    if not isinstance(limits.get("on_limit_reached"), str) or not limits["on_limit_reached"].strip():
        raise invalid("cost_limits.on_limit_reached debe ser un texto no vacío")
    voice = settings.get("voice_settings")
    required_voice_fields = {
        "stt_model", "stt_device", "stt_compute_type", "tts_engine",
        "vad_aggressiveness", "sample_rate", "wake_word_model",
        "tts_speaker_id", "tts_length_scale", "tts_noise_scale",
        "tts_noise_w_scale", "tts_pronunciation_aliases",
    }
    if not isinstance(voice, dict) or set(voice) != required_voice_fields:
        raise invalid("voice_settings debe contener exactamente las doce claves de voz requeridas")
    allowed_values = {
        "stt_model": SUPPORTED_STT_MODELS, "stt_device": ("cpu",),
        "stt_compute_type": ("int8",), "tts_engine": ("piper",),
        "wake_word_model": SUPPORTED_WAKE_WORDS,
    }
    for field, allowed in allowed_values.items():
        if not isinstance(voice[field], str) or voice[field] not in allowed:
            raise invalid(f"voice_settings.{field} debe ser uno de: {', '.join(allowed)}")
    if type(voice["vad_aggressiveness"]) is not int or voice["vad_aggressiveness"] not in range(4):
        raise invalid("voice_settings.vad_aggressiveness debe ser un entero entre 0 y 3")
    if type(voice["sample_rate"]) is not int or voice["sample_rate"] != 16000:
        raise invalid("voice_settings.sample_rate debe ser el entero 16000 en Fase 3")
    if type(voice["tts_speaker_id"]) is not int or voice["tts_speaker_id"] < 0:
        raise invalid("voice_settings.tts_speaker_id debe ser un entero no negativo, sin booleanos")
    for field, lower, upper in (
        ("tts_length_scale", 0.5, 2.0),
        ("tts_noise_scale", 0.0, 2.0),
        ("tts_noise_w_scale", 0.0, 2.0),
    ):
        value = voice[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
            raise invalid(f"voice_settings.{field} debe ser un número finito entre {lower} y {upper}, sin booleanos")
    aliases = voice["tts_pronunciation_aliases"]
    if not isinstance(aliases, dict):
        raise invalid("voice_settings.tts_pronunciation_aliases debe ser un objeto JSON")
    for source, replacement in aliases.items():
        if any(not isinstance(value, str) or not value.strip() or value != value.strip()
               for value in (source, replacement)):
            raise invalid("voice_settings.tts_pronunciation_aliases requiere claves y valores de texto no vacíos sin espacios externos")
    return settings


_SETTINGS = _load_settings(SETTINGS_PATH)


def get_model_assignment(role: str) -> dict[str, str]:
    """Return an independent provider/model mapping for a configured role."""
    if not isinstance(role, str) or role not in _SETTINGS["model_assignments"]:
        raise ConfigurationError(f"El rol solicitado {role!r} no existe en {SETTINGS_PATH}.")
    assignment = _SETTINGS["model_assignments"][role]
    return {"provider": assignment["provider"], "model": assignment["model"]}


def get_notch_settings() -> dict[str, bool | int]:
    """Return validated auto-hide settings without creating or controlling a GUI."""
    return {
        "notch_auto_hide_enabled": _SETTINGS["notch_auto_hide_enabled"],
        "notch_auto_hide_seconds": _SETTINGS["notch_auto_hide_seconds"],
    }


def get_quick_launch_shortcuts() -> list[dict[str, str]]:
    """Return independent shortcut data without launching applications."""
    return deepcopy(_SETTINGS["quick_launch_shortcuts"])


def save_notch_settings(*, auto_hide_enabled: bool, auto_hide_seconds: int,
                        shortcuts: list[dict[str, str]]) -> None:
    """Validate and atomically save only notch settings, preserving other fields.

    An explicit save rereads the file to preserve unrelated edits made on disk.
    Failed validation or publication leaves the old file and cached data intact.
    """
    global _SETTINGS
    temporary: Path | None = None
    try:
        settings = _load_settings(SETTINGS_PATH)
        settings.update(notch_auto_hide_enabled=auto_hide_enabled,
                        notch_auto_hide_seconds=auto_hide_seconds,
                        quick_launch_shortcuts=deepcopy(shortcuts))
        _validate_settings(settings, SETTINGS_PATH)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=SETTINGS_PATH.parent,
                                         prefix=".eon-settings-", suffix=".json", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, SETTINGS_PATH)
        _SETTINGS = deepcopy(settings)
    except (OSError, ValueError, TypeError) as exc:
        logger.exception("No se pudieron guardar los ajustes del notch.")
        raise ConfigurationError(f"No se pudieron guardar los ajustes: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                logger.warning("No se pudo retirar el temporal de configuración.", exc_info=True)


def get_cost_limits() -> dict[str, float | str]:
    """Return a copy of declarative cost limits without contacting paid providers."""
    return deepcopy(_SETTINGS["cost_limits"])


def get_language() -> str:
    """Return the validated user-facing language identifier."""
    return _SETTINGS["language"]


def get_voice_profile() -> str:
    """Return the stored profile selection without implementing voice features."""
    return _SETTINGS["voice_profile"]


def get_voice_settings() -> dict[str, str | int | float | dict[str, str]]:
    """Return an independent copy of strictly validated CPU-only voice settings."""
    return deepcopy(_SETTINGS["voice_settings"])
