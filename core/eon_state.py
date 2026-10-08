"""Expose assistant activity and its palette without importing any GUI toolkit."""

from enum import Enum
from types import MappingProxyType


class EonState(Enum):
    """Keep assistant activity separate from the notch's visible geometry."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    BUILDING = "building"
    ERROR = "error"
    VISION_ACTIVE = "vision_active"


# VISION_ACTIVE is a provisional visual proposal, pending Pablo's approval.
STATE_COLORS = MappingProxyType({
    EonState.IDLE: "#8ea9c7",
    EonState.LISTENING: "#00e5ff",
    EonState.THINKING: "#a742ff",
    EonState.SPEAKING: "#ff3de0",
    EonState.BUILDING: "#ff9f1c",
    EonState.ERROR: "#ff3b3b",
    EonState.VISION_ACTIVE: "#39ff88",
})

STATE_LABELS = MappingProxyType({
    EonState.IDLE: "En reposo",
    EonState.LISTENING: "Escuchando",
    EonState.THINKING: "Pensando",
    EonState.SPEAKING: "Hablando",
    EonState.BUILDING: "Construyendo",
    EonState.ERROR: "Error",
    EonState.VISION_ACTIVE: "Visión activa",
})


def state_color(state: EonState) -> str:
    """Return a hex color for an actual state, rejecting accidental strings."""
    if not isinstance(state, EonState):
        raise ValueError("El estado debe ser un valor de EonState.")
    return STATE_COLORS[state]
