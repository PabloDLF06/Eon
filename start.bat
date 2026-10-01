@echo off
REM ===========================================================================
REM  EON - Arranque de un doble clic. Sin consolas, sin terminal.
REM  pythonw = la version de Python sin ventana negra; si no existe en el
REM  entorno, se usa python a secas. Si EON muriera al nacer, esta ventana se
REM  queda y muestra el error real en espanol, en lugar de un parpadeo.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "VPY=.venv\Scripts\python.exe"
set "VPYW=.venv\Scripts\pythonw.exe"

if exist "%VPY%" goto hay_venv
echo.
echo  Parece que EON aun no esta instalado.
echo  Ejecuta primero install.bat con un doble clic.
echo.
pause
exit /b 1

:hay_venv
REM -- Si ya hay un EON en marcha, no duplicarlo ------------------------------
"%VPY%" main.py --send ping >"%TEMP%\eon_ping.txt" 2>nul
findstr /C:"pong" "%TEMP%\eon_ping.txt" >nul 2>nul
if errorlevel 1 goto lanzar
echo  EON ya esta en marcha: arriba, en el borde de la pantalla.
timeout /t 3 /nobreak >nul
exit /b 0

:lanzar
REM -- Levantar en segundo plano ----------------------------------------------
if exist "%VPYW%" goto lanzar_w
start "EON" "%VPY%" main.py
goto vigilar
:lanzar_w
start "" "%VPYW%" main.py

:vigilar
REM -- Sonda main.py --send ping cada 1 s, hasta 10 s. Sin pong: consola+pause
set "TRY=0"
:sonda
if "%TRY%"=="10" goto no_arranco
set /a TRY+=1
"%VPY%" main.py --send ping >"%TEMP%\eon_ping.txt" 2>nul
findstr /C:"pong" "%TEMP%\eon_ping.txt" >nul 2>nul
if not errorlevel 1 goto vivo
if "%TRY%"=="10" goto no_arranco
timeout /t 1 /nobreak >nul
goto sonda

:vivo
del "%TEMP%\eon_ping.txt" >nul 2>nul
exit /b 0

:no_arranco
del "%TEMP%\eon_ping.txt" >nul 2>nul
color 4A
echo.
echo  EON intento arrancar pero no sobrevivio. Lo repito en esta consola
echo  para que veas exactamente que pasa. El notch funcionara igual; la
echo  ventana negra es solo temporal, cierrala para apagar EON:
echo.
"%VPY%" main.py
echo.
echo  Si esto se cerro solo, mira logs\eon.log: doble clic desde la carpeta EON.
pause
exit /b 1
