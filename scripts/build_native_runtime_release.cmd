@echo off
setlocal

set "MSBUILD=C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\amd64\MSBuild.exe"
set "SOLUTION=%~dp0..\native_runtime\build\windows-release\stitch_runtime.sln"

if not exist "%MSBUILD%" (
  echo MSBuild not found: %MSBUILD%
  exit /b 1
)

if not exist "%SOLUTION%" (
  echo Solution not found: %SOLUTION%
  exit /b 1
)

"%MSBUILD%" "%SOLUTION%" /t:stitch_runtime /p:Configuration=Release /p:Platform=x64 /m
exit /b %ERRORLEVEL%
