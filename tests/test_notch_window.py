"""Auditoría estática (AST) de ``gui.notch_window.apply_win32_styles``.

Qt ya gestiona el layering de la isla con ``WA_TranslucentBackground``:
fijar a mano los bits de layering/composited de Win32 deja la ventana con
alfa 0 (isla invisible). El camino Win32 no se puede ejecutar en la máquina
de desarrollo (no hay ``user32``), así que el test audita el fuente: prohíbe
esos símbolos en la función y exige la forma correcta —solo
``WS_EX_TOOLWINDOW``/``WS_EX_NOACTIVATE``/``WS_EX_TRANSPARENT``, reescritura
únicamente si el estilo cambia y remate con
``SetWindowPos(HWND_TOPMOST, SWP_NOSIZE|SWP_NOMOVE|SWP_FRAMECHANGED)``.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "gui" / "notch_window.py"

#: Bits que Qt gestiona solo; tocarlos a mano deja la ventana con alfa 0.
FORBIDDEN_SYMBOLS = {"WS_EX_LAYERED", "WS_EX_COMPOSITED"}
#: Los únicos estilos extendidos que la función debe aplicar.
REQUIRED_SYMBOLS = {"WS_EX_TOOLWINDOW", "WS_EX_NOACTIVATE", "WS_EX_TRANSPARENT"}
#: El remate: reafirmar topmost sin mover ni redimensionar.
TOPMOST_FINISH = {"SetWindowPos", "HWND_TOPMOST", "SWP_NOSIZE", "SWP_NOMOVE", "SWP_FRAMECHANGED"}

#: La versión antigua de la función (layering/composited a mano): la auditoría
#: tiene que rechazarla, igual que el auditor de .bat rechaza el estilo viejo.
OLD_BROKEN_FUNCTION = (
    "def apply_win32_styles(hwnd: int, transparent: bool) -> bool:\n"
    "    style = 0\n"
    "    style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_COMPOSITED\n"
    "    style = (style | WS_EX_TRANSPARENT) if transparent else (style & ~WS_EX_TRANSPARENT)\n"
    "    return True\n"
)


def _function_node(name: str) -> ast.FunctionDef:
    """Nodo ``FunctionDef`` de ``name`` desde el fuente, sin importar el módulo.

    Importar ``gui.notch_window`` arrastraría PyQt6 (no hace falta para auditar
    el fuente y en la máquina de desarrollo puede no estar).
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"falta la función {name!r} en {MODULE}")


def _symbols(node: ast.AST) -> set[str]:
    """Identificadores usados en el subárbol (``Name`` y ``Attribute``)."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


class TestApplyWin32StylesAst:
    """El AST de ``apply_win32_styles`` nunca toca los bits de layering."""

    def test_function_exists_in_source(self):
        assert MODULE.is_file(), f"falta {MODULE}"
        assert _function_node("apply_win32_styles").name == "apply_win32_styles"

    def test_forbids_layered_and_composited(self):
        func = _function_node("apply_win32_styles")
        offenders = sorted(_symbols(func) & FORBIDDEN_SYMBOLS)
        assert not offenders, (
            f"apply_win32_styles toca bits que Qt gestiona (alfa 0 = isla invisible): {offenders}"
        )

    def test_only_applies_toolwindow_noactivate_transparent(self):
        func = _function_node("apply_win32_styles")
        missing = sorted(REQUIRED_SYMBOLS - _symbols(func))
        assert not missing, f"apply_win32_styles no aplica los estilos que caben: {missing}"

    def test_finishes_with_setwindowpos_topmost_framechanged(self):
        func = _function_node("apply_win32_styles")
        missing = sorted(TOPMOST_FINISH - _symbols(func))
        assert not missing, f"el remate SetWindowPos no está completo: faltan {missing}"

    def test_audit_rejects_the_old_broken_function(self):
        # control negativo: si alguien reintroduce la versión vieja, el test salta
        func = ast.parse(OLD_BROKEN_FUNCTION).body[0]
        offenders = sorted(_symbols(func) & FORBIDDEN_SYMBOLS)
        assert offenders == sorted(FORBIDDEN_SYMBOLS), "la auditoría no detecta la versión vieja"
