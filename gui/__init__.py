"""Paquete de interfaz de EON.

Mantiene el __init__ deliberadamente ligero: los módulos con dependencias de
``PyQt6`` (``notch_window``, ``char_widget``, ``screen_glow``) se importan sólo
cuando la aplicación arranca con GUI, mientras que la capa geométrica
(``scene``, ``notch_layout``, ``char_kinematics``) es Python puro y se puede
usar en tests, herramientas de previsualización o en máquinas sin monitor.
"""

from __future__ import annotations

__all__ = ["char_kinematics", "notch_layout", "scene"]
