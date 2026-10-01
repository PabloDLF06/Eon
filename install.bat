@echo off
REM ===========================================================================
REM  EON - Instalador de un solo doble clic. Windows 11, 64 bits.
REM  Comprueba Python y Git, monta el entorno virtual, instala dependencias,
REM  verifica Ollama y descarga los modelos. Todo en espanol, sin terminales.
REM ===========================================================================
setlocal EnableExtensions
title EON - Instalacion
cd /d "%~dp0"

echo.
echo  EON . Instalacion
echo  -----------------
echo.

REM ---------------------------------------------------------------- 1 de 5 : Python
set "PYTHON="
py -3.12 -c "pass" >nul 2>nul && set "PYTHON=py -3.12"
if not defined PYTHON py -3.11 -c "pass" >nul 2>nul && set "PYTHON=py -3.11"
if not defined PYTHON py -3 -c "pass" >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON python -c "pass" >nul 2>nul && set "PYTHON=python"
if not defined PYTHON goto sin_python
%PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto python_viejo

REM ---------------------------------------------------------------- 2 de 5 : Git
where git >nul 2>nul
if errorlevel 1 goto sin_git

REM ---------------------------------------------------------------- 3 de 5 : Ollama
echo  Comprobando Ollama, el motor que hace pensar a EON...
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -UseBasicParsing -TimeoutSec 3).StatusCode } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 goto ollama_ok
echo.
echo  [AVISO] Ollama no responde en http://localhost:11434
echo          Instalalo desde https://ollama.com/download y vuelve a ejecutar
echo          este instalador. EON puede instalarse igualmente, pero no
echo          pensara hasta que Ollama este en marcha.
:ollama_ok

REM ---------------------------------------------------------------- 4 de 5 : entorno
echo  Creando el entorno virtual .venv: un momento, no borra nada tuyo...
if exist ".venv\Scripts\python.exe" goto venv_existe
%PYTHON% -m venv .venv
if errorlevel 1 goto sin_python
goto venv_listo
:venv_existe
echo  .venv ya existe: se reutiliza y se actualiza.
:venv_listo
set "VPY=.venv\Scripts\python.exe"

echo  Actualizando pip...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check

echo  Instalando dependencias: la primera vez tarda unos minutos...
"%VPY%" -m pip install -r requirements.txt --disable-pip-version-check --quiet
if not errorlevel 1 goto deps_ok
echo.
echo  El paquete completo no se pudo instalar en un intento; probando el
echo  paquete esencial: EON funcionara sin voz pero seguira siendo util...
"%VPY%" -m pip install -r requirements-core.txt --disable-pip-version-check --quiet
if errorlevel 1 goto sin_pip
:deps_ok

REM ---------------------------------------------------------------- 5 de 5 : modelos
where ollama >nul 2>nul
if errorlevel 1 goto sin_modelos
echo.
echo  Descargando modelos locales, solo la primera vez, con calma:
echo    - qwen2.5-coder:7b        el programador, ~4.7 GB
ollama pull qwen2.5-coder:7b
echo    - llama3.2-vision:latest   los ojos, ~7.9 GB
ollama pull llama3.2-vision:latest
echo    - llama3.1:8b              el cerebro, ~4.9 GB
ollama list | findstr /i "llama3.1:8b" >nul
if errorlevel 1 ollama pull llama3.1:8b
:sin_modelos

REM ---------------------------------------------------------------- verificacion
"%VPY%" -c "import config; r = config.init_logging(); import tests.test_core" >nul 2>nul
echo.
echo  Comprobacion rapida del nucleo...
"%VPY%" -m pytest tests/ -q --no-header -p no:cacheprovider
if not errorlevel 1 goto tests_ok
echo.
echo  [AVISO] Alguna comprobacion no paso. EON puede funcionar igualmente,
echo          pero mandame la captura de esta ventana si quieres que lo mire.
:tests_ok

color 2A
echo.
echo  ============================================================
echo    OK. Todo listo, senor. EON esta instalado.
echo    Ejecute start.bat con un doble clic para verle aparecer
echo    por el borde superior de la pantalla.
echo  ============================================================
echo.
pause
exit /b 0

:sin_python
color 4A
echo.
echo  [ERROR] No encuentro Python 3.10 o superior.
echo          Instalalo desde https://www.python.org/downloads/
echo          y MARCA la casilla "Add python.exe to PATH" al instalar.
echo          Luego vuelve a ejecutar este instalador.
echo.
pause
exit /b 1

:python_viejo
color 4A
echo.
echo  [ERROR] Python es demasiado antiguo: hace falta 3.10 o superior.
echo          Descarga el actual desde https://www.python.org/downloads/
echo          recordando marcar "Add python.exe to PATH".
echo.
pause
exit /b 1

:sin_git
color 4A
echo.
echo  [ERROR] Falta Git: EON lo usa para mejorarse a si mismo de forma segura.
echo          Instalalo desde https://git-scm.com/download/win y repite.
echo.
pause
exit /b 1

:sin_pip
color 4A
echo.
echo  [ERROR] No se pudo instalar ninguna de las dos tandas de dependencias.
echo          Suele ser el antivirus o la conexion. Prueba a pausar el
echo          antivirus un momento y ejecuta este instalador otra vez.
echo.
pause
exit /b 1
