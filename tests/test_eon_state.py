"""Verify the toolkit-independent palette and all seven public activities."""

import re

import pytest

from core.eon_state import EonState, STATE_COLORS, STATE_LABELS, state_color


def test_seven_unique_states_and_complete_palette() -> None:
    assert [state.name for state in EonState] == ["IDLE", "LISTENING", "THINKING", "SPEAKING", "BUILDING", "ERROR", "VISION_ACTIVE"]
    assert set(STATE_COLORS) == set(EonState) == set(STATE_LABELS)
    assert len(set(STATE_COLORS.values())) == 7
    assert all(re.fullmatch(r"#[0-9a-f]{6}", color) for color in STATE_COLORS.values())


@pytest.mark.parametrize("state,color", [
    (EonState.IDLE, "#8ea9c7"), (EonState.LISTENING, "#00e5ff"),
    (EonState.THINKING, "#a742ff"), (EonState.SPEAKING, "#ff3de0"),
    (EonState.BUILDING, "#ff9f1c"), (EonState.ERROR, "#ff3b3b"),
    (EonState.VISION_ACTIVE, "#39ff88"),
])
def test_exact_state_colors(state: EonState, color: str) -> None:
    assert state_color(state) == color


@pytest.mark.parametrize("value", [None, "idle", 0, True])
def test_invalid_state_is_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        state_color(value)


def test_palette_is_read_only() -> None:
    with pytest.raises(TypeError):
        STATE_COLORS[EonState.IDLE] = "#000000"
