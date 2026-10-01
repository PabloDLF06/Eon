"""Núcleo de EON: oído, voz, juicio, manos y fábrica.

Cada módulo es independiente de Qt y degrada por sí solo si falta un
periférico. Este ``__init__`` no reexporta nada a propósito: los consumidores
importan el módulo concreto (``from core.voice_engine import VoiceEngine``), lo
que mantiene el arranque rápido y evita que un fallo aislado arrastre a todo
el paquete.
"""

from __future__ import annotations

__all__ = [
    "acoustic_detector",
    "app_builder",
    "barge_in",
    "event_bus",
    "model_router",
    "self_programmer",
    "vision_actuator",
    "voice_engine",
]
