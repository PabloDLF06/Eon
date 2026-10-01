"""Auditor estático de start.bat e install.bat.

cmd.exe parsea los bloques ``if ( ... )`` antes de ejecutarlos. Un
``echo ... (texto)`` dentro del bloque cierra el ``if`` en el primer ``)`` y
explota con «No se esperaba )». Los lanzadores tienen que ir en estilo lineal:
solo etiquetas + goto, ASCII puro, CRLF, sin delayed expansion y sin
redirecciones en las líneas echo.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LAUNCHERS = ("start.bat", "install.bat")

# Recorte del start.bat que vivía en main: bloques if/for, delayed expansion y
# paréntesis en textos echo. El auditor tiene que rechazarlo.
OLD_BROKEN_START_BAT = (
    "\r\n".join(
        [
            "@echo off",
            "setlocal EnableExtensions EnableDelayedExpansion",
            'cd /d "%~dp0"',
            'set "VPY=.venv\\Scripts\\python.exe"',
            'set "VPYW=.venv\\Scripts\\pythonw.exe"',
            'if not exist "%VPY%" (',
            "    echo  Parece que EON aun no esta instalado.",
            "    echo  Ejecuta primero install.bat con un doble clic.",
            "    echo.",
            "    pause",
            "    exit /b 1",
            ")",
            '"%VPYW%" main.py --send "ping" >"%TEMP%\\eon_ping.txt" 2>nul',
            'findstr /C:"pong" "%TEMP%\\eon_ping.txt" >nul',
            "if not errorlevel 1 (",
            "    echo  EON ya esta en marcha (arriba, en el borde de la pantalla).",
            "    timeout /t 3 >nul",
            "    exit /b 0",
            ")",
            'if exist "%VPYW%" (',
            '    start "" "%VPYW%" main.py',
            ") else (",
            '    start "EON" "%VPY%" main.py',
            ")",
            'set "ALIVE=0"',
            "for /L %%i in (1,1,10) do (",
            '    if "!ALIVE!"=="0" (',
            "        ping -n 2 127.0.0.1 >nul",
            '        "%VPY%" main.py --send "ping" >"%TEMP%\\eon_ping.txt" 2>nul',
            '        findstr /C:"pong" "%TEMP%\\eon_ping.txt" >nul && set "ALIVE=1"',
            "    )",
            ")",
            'if "!ALIVE!"=="1" exit /b 0',
            "echo  para que veas exactamente que pasa (el notch funcionara igual; la",
            '"%VPY%" main.py',
            "pause",
            "exit /b 1",
        ]
    )
    + "\r\n"
)

OLD_BROKEN_INSTALL_BAT = (
    "\r\n".join(
        [
            "@echo off",
            "setlocal EnableExtensions EnableDelayedExpansion",
            "if errorlevel 1 (",
            "    echo  [AVISO] Ollama no responde en http://localhost:11434",
            "    echo          este instalador. EON puede instalarse igualmente, pero no",
            ")",
            'if exist ".venv\\Scripts\\python.exe" (',
            "    echo  .venv ya existe: se reutiliza y se actualiza.",
            ") else (",
            "    python -m venv .venv",
            "    if errorlevel 1 goto sin_python",
            ")",
            "if errorlevel 1 (",
            "    echo  El paquete esencial (EON funcionara sin voz pero seguira siendo util)...",
            "    echo  [ERROR] Python es demasiado antiguo (hace falta 3.10 o superior).",
            ")",
            "exit /b 0",
        ]
    )
    + "\r\n"
)


def audit_windows_bat(data: bytes, *, name: str = "launcher.bat") -> list[str]:
    """Devuelve problemas de estilo cmd.exe lineal / ASCII / CRLF. Vacío = OK."""
    issues: list[str] = []
    if not data:
        return [f"{name}: archivo vacio"]

    non_ascii = next((i for i, byte in enumerate(data) if byte > 127), None)
    if non_ascii is not None:
        issues.append(f"{name}: no es ASCII puro (offset {non_ascii})")

    newline_issue = _crlf_problem(data)
    if newline_issue:
        issues.append(f"{name}: {newline_issue}")

    text = data.decode("ascii", errors="replace")
    if re.search(r"(?i)delayedexpansion", text):
        issues.append(f"{name}: usa delayed expansion")
    if re.search(r"![A-Za-z_][A-Za-z0-9_]*!", text):
        issues.append(f"{name}: usa expansion retardada !VAR!")

    for lineno, line in enumerate(text.splitlines(), 1):
        issues.extend(_line_issues(name, lineno, line))
    return issues


def _crlf_problem(data: bytes) -> str | None:
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        if byte == 10:
            return "tiene LF suelto; hace falta CRLF"
        if byte == 13:
            if index + 1 >= length or data[index + 1] != 10:
                return "tiene CR suelto; hace falta CRLF"
            index += 2
            continue
        index += 1
    return None


def _line_issues(name: str, lineno: int, line: str) -> list[str]:
    found: list[str] = []
    in_quotes = False
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif char in "()" and not in_quotes:
            snippet = line.strip()[:90]
            found.append(f"{name}:L{lineno}: parentesis fuera de comillas: {snippet}")
            break
    if in_quotes:
        found.append(f"{name}:L{lineno}: comillas sin cerrar")

    stripped = line.lstrip()
    if re.match(r"^@?echo\s+(on|off)\s*$", stripped, re.I):
        return found
    echo_match = re.match(r"^@?echo(.*)$", stripped, re.I)
    if echo_match:
        body = echo_match.group(1)
        quoted = False
        for char in body:
            if char == '"':
                quoted = not quoted
            elif char in "<>" and not quoted:
                snippet = stripped[:90]
                found.append(f"{name}:L{lineno}: redireccion en echo: {snippet}")
                break
    return found


def _bat_text(name: str) -> str:
    return (ROOT / name).read_bytes().decode("ascii")


def _code_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("REM") or stripped.startswith("::"):
            continue
        lines.append(stripped)
    return lines


class TestWindowsLaunchers:
    """Audita start.bat e install.bat y rechaza el estilo que rompe cmd.exe."""

    def test_start_and_install_pass_linear_ascii_crlf_audit(self):
        for name in LAUNCHERS:
            path = ROOT / name
            assert path.is_file(), f"falta {name}"
            issues = audit_windows_bat(path.read_bytes(), name=name)
            assert issues == [], f"{name} no pasa la auditoria: " + "; ".join(issues)

    def test_start_bat_polls_ping_until_10s_then_console_and_pause(self):
        text = _bat_text("start.bat")
        compact = text.replace('"', "")
        assert re.search(r"main\.py\s+--send\s+ping", compact), "debe sondear main.py --send ping"
        assert "pong" in text
        assert re.search(r'==\s*"10"', text), "la sonda debe limitarse a 10 s / 10 intentos"

        blocking: list[str] = []
        for line in _code_lines(text):
            if re.search(r"\bstart\b", line, re.I):
                continue
            if "--send" in line:
                continue
            if re.search(r"main\.py\s*$", line):
                blocking.append(line)
        assert blocking, "si EON no contesta debe relanzar main.py en esta consola"
        assert any(re.match(r"(?i)^pause\b", line) for line in _code_lines(text))

    def test_old_start_bat_fails_audit(self):
        issues = audit_windows_bat(OLD_BROKEN_START_BAT.encode("ascii"), name="start.bat")
        joined = " | ".join(issues)
        assert issues, "el start.bat viejo tiene que fallar la auditoria"
        assert any("parentesis" in item.lower() for item in issues), joined
        assert any("delayed" in item.lower() or "retardad" in item.lower() for item in issues), joined

    def test_old_install_bat_fails_audit(self):
        issues = audit_windows_bat(OLD_BROKEN_INSTALL_BAT.encode("ascii"), name="install.bat")
        joined = " | ".join(issues)
        assert issues, "el install.bat viejo tiene que fallar la auditoria"
        assert any("parentesis" in item.lower() for item in issues), joined
        assert any("delayed" in item.lower() or "retardad" in item.lower() for item in issues), joined

    def test_linear_if_goto_without_parens_passes(self):
        payload = (
            "\r\n".join(
                [
                    "@echo off",
                    'if exist "x" goto hay',
                    "echo falta",
                    "pause",
                    "exit /b 1",
                    ":hay",
                    "exit /b 0",
                ]
            )
            + "\r\n"
        )
        assert audit_windows_bat(payload.encode("ascii")) == []

    def test_parens_inside_quotes_are_allowed(self):
        payload = '@echo off\r\npython -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"\r\n'
        assert audit_windows_bat(payload.encode("ascii")) == []

    def test_unix_lf_fails(self):
        issues = audit_windows_bat(b"@echo off\nexit /b 0\n", name="lf.bat")
        assert any("CRLF" in item or "LF" in item for item in issues)

    def test_non_ascii_fails(self):
        payload = "@echo off\r\necho café\r\n".encode()
        issues = audit_windows_bat(payload, name="utf8.bat")
        assert any("ascii" in item.lower() for item in issues)

    def test_echo_redirection_fails(self):
        payload = b"@echo off\r\necho hola >nul\r\n"
        issues = audit_windows_bat(payload, name="redir.bat")
        assert any("redireccion" in item.lower() for item in issues)
