"""Módulos de seguridad de EON.

Contiene únicamente el interruptor de emergencia. El paquete está aislado a
propósito: es el único que la auto-programación no puede tocar y por eso es
también el único escrito sin dependencias externas.
"""

from __future__ import annotations

from safety.killswitch import (
    EonPaused,
    IntegrityReport,
    KillSwitch,
    assert_writable_path,
    build_default_killswitch,
    compute_file_sha256,
    verify_integrity,
)

__all__ = [
    "EonPaused",
    "IntegrityReport",
    "KillSwitch",
    "assert_writable_path",
    "build_default_killswitch",
    "compute_file_sha256",
    "verify_integrity",
]
