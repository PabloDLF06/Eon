"""Regresiones del autoarranque: construir apps no abre periféricos bajo pytest."""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest
from core.app_builder import AppBuilder, BuildResult


@pytest.fixture
def launchers(monkeypatch):
    """Espías en ambos caminos: ni navegadores, ni procesos, ni servidores reales."""
    mocks = {stack: Mock(return_value=True) for stack in ("web", "python")}
    for stack, mock in mocks.items():
        monkeypatch.setattr(AppBuilder, f"_launch_{stack}", mock)
    return mocks


def test_auto_open_defaults_to_true(tmp_path):
    assert AppBuilder(projects_root=tmp_path).auto_open is True


@pytest.mark.parametrize("stack", ["web", "python"])
def test_create_does_not_launch_under_pytest(tmp_path, launchers, stack):
    assert os.environ.get("PYTEST_CURRENT_TEST") is not None
    builder = AppBuilder(projects_root=tmp_path, auto_open=True)

    result = builder.create("una app de notas", stack=stack)

    assert result.ok, result.detail
    assert (tmp_path / result.slug / result.entry).is_file()
    assert result.launched is False
    assert result.url == ""
    assert builder._servers == []
    for launcher in launchers.values():
        launcher.assert_not_called()


@pytest.mark.parametrize("entry", ["index.html", "app.py"])
def test_open_existing_does_not_launch_under_pytest(tmp_path, launchers, entry):
    assert os.environ.get("PYTEST_CURRENT_TEST") is not None
    builder = AppBuilder(projects_root=tmp_path, auto_open=True)
    project = tmp_path / "existing"
    project.mkdir()
    (project / entry).write_text("", encoding="utf-8")

    result = builder.open_existing("existing")

    assert result.ok
    assert result.entry == entry
    assert result.launched is False
    assert result.url == ""
    assert builder._servers == []
    for launcher in launchers.values():
        launcher.assert_not_called()


@pytest.mark.parametrize("entry", ["index.html", "app.py"])
def test_even_an_empty_pytest_variable_suppresses_launch(tmp_path, monkeypatch, launchers, entry):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    builder = AppBuilder(projects_root=tmp_path, auto_open=True)
    result = BuildResult(True, directory=str(tmp_path), entry=entry)

    builder._launch(result, tmp_path, entry)

    assert result.launched is False
    for launcher in launchers.values():
        launcher.assert_not_called()


@pytest.mark.parametrize("stack, entry", [("web", "index.html"), ("python", "app.py")])
@pytest.mark.parametrize("auto_open", [False, True])
def test_launch_respects_auto_open_outside_pytest(tmp_path, monkeypatch, launchers, stack, entry, auto_open):
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    builder = AppBuilder(projects_root=tmp_path, auto_open=auto_open)
    result = BuildResult(True, directory=str(tmp_path), entry=entry)

    builder._launch(result, tmp_path, entry)

    assert result.launched is auto_open
    if auto_open:
        args = (tmp_path, entry, result) if stack == "web" else (tmp_path, entry)
        launchers[stack].assert_called_once_with(*args)
        launchers["python" if stack == "web" else "web"].assert_not_called()
    else:
        for launcher in launchers.values():
            launcher.assert_not_called()


def test_empty_entry_is_not_launched_outside_pytest(tmp_path, monkeypatch, launchers):
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    builder = AppBuilder(projects_root=tmp_path, auto_open=True)
    result = BuildResult(True)

    builder._launch(result, tmp_path, "")

    assert result.launched is False
    for launcher in launchers.values():
        launcher.assert_not_called()
