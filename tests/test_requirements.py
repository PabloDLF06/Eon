"""La dependencia opcional de VAD no debe pedir compilación en Python moderno."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_webrtcvad_wheels_is_only_installed_below_python_313():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    vad_requirements = [line.strip() for line in lines if line.strip().startswith("webrtcvad-wheels")]
    assert len(vad_requirements) == 1, "debe haber una única dependencia webrtcvad-wheels"
    _requirement, separator, marker = vad_requirements[0].partition(";")
    assert separator == ";", "webrtcvad-wheels necesita una marca de versión Python"
    actual = ast.parse(marker.strip(), mode="eval")
    expected = ast.parse('python_version < "3.13"', mode="eval")
    assert ast.dump(actual) == ast.dump(expected), "el VAD nativo debe excluir Python 3.13 y posteriores"
