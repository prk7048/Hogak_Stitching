@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv312\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo Python venv not found: %PYTHON%
  exit /b 1
)

cd /d "%ROOT%"
"%PYTHON%" scripts\native_runtime_soak.py %*
exit /b %ERRORLEVEL%
