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
        assert not offenders, f"apply_win32_styles toca bits que Qt gestiona (alfa 0 = isla invisible): {offenders}"

    def test_only_applies_toolwindow_noactivate_transparent(self):
        func = _function_node("apply_win32_styles")
        styles = {symbol for symbol in _symbols(func) if symbol.startswith("WS_EX_")}
        assert styles == REQUIRED_SYMBOLS, f"estilos extendidos inesperados o ausentes: {styles ^ REQUIRED_SYMBOLS}"

    def test_rewrites_style_only_if_it_changes(self):
        func = _function_node("apply_win32_styles")
        writes = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setter"
        ]
        assert len(writes) == 1, "debe haber una única escritura del estilo"
        expected_guard = ast.parse("new_style != style", mode="eval").body
        guards = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.If) and ast.dump(node.test) == ast.dump(expected_guard)
        ]
        assert len(guards) == 1, "el estilo solo debe reescribirse si new_style != style"
        assert any(writes[0] is node for stmt in guards[0].body for node in ast.walk(stmt)), (
            "la escritura del estilo no está dentro de la guarda de cambio"
        )

    def test_finishes_with_setwindowpos_topmost_framechanged(self):
        func = _function_node("apply_win32_styles")
        missing = sorted(TOPMOST_FINISH - _symbols(func))
        assert not missing, f"el remate SetWindowPos no está completo: faltan {missing}"
        calls = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "SetWindowPos"
        ]
        assert len(calls) == 1, "debe haber un único remate SetWindowPos"
        call = calls[0]
        expected_args = ast.parse(
            "finish(ctypes.c_void_p(int(hwnd)), ctypes.c_void_p(HWND_TOPMOST), "
            "0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_FRAMECHANGED)",
            mode="eval",
        ).body.args
        assert not call.keywords
        assert [ast.dump(arg) for arg in call.args] == [ast.dump(arg) for arg in expected_args], (
            "SetWindowPos debe reafirmar topmost sin mover ni redimensionar, con FRAMECHANGED"
        )
        blocks = [node for node in ast.walk(func) if isinstance(node, ast.Try)]
        assert any(
            isinstance(block.body[-2], ast.Expr)
            and block.body[-2].value is call
            and isinstance(block.body[-1], ast.Return)
            and isinstance(block.body[-1].value, ast.Constant)
            and block.body[-1].value.value is True
            for block in blocks
            if len(block.body) >= 2
        ), "SetWindowPos debe rematar el camino Win32 antes de devolver True"

    def test_audit_rejects_the_old_broken_function(self):
        # control negativo: si alguien reintroduce la versión vieja, el test salta
        func = ast.parse(OLD_BROKEN_FUNCTION).body[0]
        offenders = sorted(_symbols(func) & FORBIDDEN_SYMBOLS)
        assert offenders == sorted(FORBIDDEN_SYMBOLS), "la auditoría no detecta la versión vieja"
