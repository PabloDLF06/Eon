@echo off
setlocal
chcp 65001 >nul
echo EON - Comprobaciones previas de Fase 0.
set "EON_CHECK_FAILED=0"

powershell.exe -NoLogo -NoProfile -Command "$ErrorActionPreference = 'Stop'; $pythonCandidates = @(@{ Name = 'py'; Arguments = @('-3', '--version') }, @{ Name = 'python'; Arguments = @('--version') }); foreach ($candidate in $pythonCandidates) { $command = Get-Command $candidate.Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1; if ($null -eq $command) { continue }; try { $versionOutput = (& $command.Source @($candidate.Arguments) 2>&1 | Out-String).Trim(); if ($LASTEXITCODE -ne 0) { continue }; if ($versionOutput -match '^Python (\d+)\.(\d+)(?:\.|$)') { $major = [int]$Matches[1]; $minor = [int]$Matches[2]; if (($major -gt 3) -or (($major -eq 3) -and ($minor -ge 10))) { Write-Host ('[OK] ' + $versionOutput + ' cumple Python >= 3.10.'); exit 0 } } } catch { continue } }; Write-Host '[ERROR] No se encuentra un Python >= 3.10 utilizable.'; exit 1"
if errorlevel 1 set "EON_CHECK_FAILED=1"

git --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git no está disponible o no responde.
    set "EON_CHECK_FAILED=1"
) else (
    echo [OK] Git está disponible.
)

powershell.exe -NoLogo -NoProfile -Command "$ErrorActionPreference = 'Stop'; try { $response = Invoke-RestMethod -Uri 'http://localhost:11434/api/version' -TimeoutSec 3; if ([string]::IsNullOrWhiteSpace([string]$response.version)) { throw 'Respuesta sin versión' }; Write-Host ('[OK] Ollama responde en localhost:11434. Versión: ' + $response.version); exit 0 } catch { Write-Host '[ERROR] Ollama no responde con una versión válida en localhost:11434.'; exit 1 }"
if errorlevel 1 set "EON_CHECK_FAILED=1"

echo Fase 0: solo comprobaciones; EON todavía está en construcción.
exit /b %EON_CHECK_FAILED%
