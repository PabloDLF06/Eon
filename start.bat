@echo off
REM ===========================================================================
REM  EON - Arranque de un doble clic. Sin consolas, sin terminal.
REM  pythonw = la version de Python sin ventana negra; si no existe, se usa
REM  python y al cerrar la consola EON se apaga (comportamiento esperado).
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo  Parece que EON aun no esta instalado.
    echo  Ejecuta primero install.bat con un doble clic.
    echo.
    pause
    exit /b 1
)

REM Si ya hay un EON en marcha, no duplicarlo: su propio aviso lo indica.
".venv\Scripts\pythonw.exe" main.py --send "ping" >"%TEMP%\eon_ping.txt" 2>nul
findstr /C:"pong" "%TEMP%\eon_ping.txt" >nul
if not errorlevel 1 (
    echo  EON ya esta en marcha (arriba, en el borde de la pantalla).
    timeout /t 3 >nul
    exit /b 0
)

start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0
