@echo off
setlocal EnableExtensions

set "ROOT=%~dp0.."
cd /d "%ROOT%"

call :reset_dir_keep_gitkeep "output\debug"

echo Cleaning output\native generated artifacts ...
if exist "output\native\redesign_transmit.ts" del /f /q "output\native\redesign_transmit.ts" >nul 2>&1
if exist "output\native\verify_production_output_manual.ts" del /f /q "output\native\verify_production_output_manual.ts" >nul 2>&1
if exist "output\native\calibration" rmdir /s /q "output\native\calibration"
if exist "output\native\soak" rmdir /s /q "output\native\soak"

echo Cleaning project __pycache__ directories ...
for /d /r "scripts" %%D in (__pycache__) do if exist "%%~fD" rmdir /s /q "%%~fD"
for /d /r "stitching" %%D in (__pycache__) do if exist "%%~fD" rmdir /s /q "%%~fD"

echo Done.
exit /b 0

:reset_dir_keep_gitkeep
echo Cleaning %~1 ...
if exist "%~1" (
  del /f /q "%~1\.gitkeep" >nul 2>&1
  rmdir /s /q "%~1"
)
mkdir "%~1" >nul 2>&1
type nul > "%~1\.gitkeep"
exit /b 0
