@echo off
REM ===========================================================================
REM  EON - Arranque de un doble clic. Sin consolas, sin terminal.
REM  pythonw = la version de Python sin ventana negra; si no existe en el
REM  entorno, se usa python a secas. Si EON muriera al nacer, esta ventana se
REM  queda y muestra el error real en espanol (en lugar de un parpadeo).
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "VPY=.venv\Scripts\python.exe"
set "VPYW=.venv\Scripts\pythonw.exe"

if not exist "%VPY%" (
    echo  Parece que EON aun no esta instalado.
    echo  Ejecuta primero install.bat con un doble clic.
    echo.
    pause
    exit /b 1
)

REM -- Si ya hay un EON en marcha, no duplicarlo ------------------------------
"%VPYW%" main.py --send "ping" >"%TEMP%\eon_ping.txt" 2>nul
if not exist "%VPYW%" "%VPY%" main.py --send "ping" >"%TEMP%\eon_ping.txt" 2>nul
findstr /C:"pong" "%TEMP%\eon_ping.txt" >nul
if not errorlevel 1 (
    echo  EON ya esta en marcha (arriba, en el borde de la pantalla).
    timeout /t 3 >nul
    exit /b 0
)

REM -- Levantar en segundo plano ----------------------------------------------
if exist "%VPYW%" (
    start "" "%VPYW%" main.py
) else (
    start "EON" "%VPY%" main.py
)

REM -- Vigilar: si al cabo de unos segundos no contesta, mostrar el error -----
set "ALIVE=0"
for /L %%i in (1,1,10) do (
    if "!ALIVE!"=="0" (
        ping -n 2 127.0.0.1 >nul
        if exist "%VPYW%" (
            "%VPYW%" main.py --send "ping" >"%TEMP%\eon_ping.txt" 2>nul
        ) else (
            "%VPY%" main.py --send "ping" >"%TEMP%\eon_ping.txt" 2>nul
        )
        findstr /C:"pong" "%TEMP%\eon_ping.txt" >nul && set "ALIVE=1"
    )
)
del "%TEMP%\eon_ping.txt" >nul 2>nul

if "!ALIVE!"=="1" exit /b 0

color 4A
echo.
echo  EON intento arrancar pero no sobrevivio. Lo repito en esta consola
echo  para que veas exactamente que pasa (el notch funcionara igual; la
echo  ventana negra es solo temporal, cierrala para apagar EON):
echo.
"%VPY%" main.py
echo.
echo  Si esto se cerro solo, mira logs\eon.log (doble clic desde la carpeta EON).
pause
exit /b 1
