#!/usr/bin/env python3
"""Regenera la línea base de integridad del interruptor de seguridad.

``safety/killswitch.py`` se autoverifica con SHA-256 en cada arranque: si el
archivo cambia sin este certificado, EON se niega a arrancar. Ese es el trato
para que ni el auto-programador pueda tocar el kill switch en secreto.

Por eso este utilitario es **humano y deliberado**: se ejecuta a mano cuando
*uno mismo* haya editado el interruptor.

    python tools/attest_killswitch.py            # escribe assets/integrity/killswitch.sha256
    python tools/attest_killswitch.py --check    # sólo informa, no escribe

El formato es el de ``sha256sum`` (``<hash>  safety/killswitch.py``) para que
``certutil`` y GNU coreutils lo verifiquen igual.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safety.killswitch import compute_file_sha256, killswitch_path

BASELINE = ROOT / "assets" / "integrity" / "killswitch.sha256"


def current_hash() -> str:
    return compute_file_sha256(killswitch_path())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="sólo compara con la línea base existente")
    args = parser.parse_args(argv)

    source = killswitch_path()
    if not source.exists():
        print(f"No encuentro {source}", file=sys.stderr)
        return 1
    actual = current_hash()
    baseline_text = BASELINE.read_text(encoding="utf-8").strip() if BASELINE.exists() else ""
    expected = baseline_text.split()[0] if baseline_text else ""

    if args.check:
        if not expected:
            print("Sin línea base todavía. Ejecuta: python tools/attest_killswitch.py")
            return 1
        if expected == actual:
            print(f"OK: killswitch verificado ({actual[:16]}…)")
            return 0
        print(f"DISCREPANCIA:\n  guardado: {expected}\n  actual:   {actual}", file=sys.stderr)
        print("Si la edición es tuya y legítima: python tools/attest_killswitch.py", file=sys.stderr)
        return 2

    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(f"{actual}  safety/killswitch.py\n", encoding="utf-8")
    print(f"Escrito {BASELINE.relative_to(ROOT)}: {actual}")
    if expected and expected != actual:
        print("(la línea base anterior difería: recuerda commitear las dos cosas juntas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
