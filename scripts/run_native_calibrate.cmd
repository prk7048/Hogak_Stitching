@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv312\Scripts\python.exe"
set "OUT_FILE=%ROOT%\output\native\runtime_homography.json"
set "DEBUG_DIR=%ROOT%\output\native\calibration"

if not exist "%PYTHON%" (
  echo Python venv not found: %PYTHON%
  exit /b 1
)

cd /d "%ROOT%"

if /I "%~1"=="--calibration-only" (
  shift
  goto calibrate_only
)

"%PYTHON%" -m stitching.cli native-calibrate ^
  --out "%OUT_FILE%" ^
  --debug-dir "%DEBUG_DIR%" ^
  --launch-runtime ^
  %*

exit /b %ERRORLEVEL%

:calibrate_only
"%PYTHON%" -m stitching.cli native-calibrate ^
  --out "%OUT_FILE%" ^
  --debug-dir "%DEBUG_DIR%" ^
  %*

exit /b %ERRORLEVEL%
