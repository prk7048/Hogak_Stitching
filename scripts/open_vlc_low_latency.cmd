@echo off
setlocal

set "MODE=%~1"
if not defined MODE set "MODE=transmit"
set "CACHE_MS=%~2"
if not defined CACHE_MS set "CACHE_MS=120"

set "VLC_BIN="
if exist "C:\Program Files\VideoLAN\VLC\vlc.exe" set "VLC_BIN=C:\Program Files\VideoLAN\VLC\vlc.exe"
if not defined VLC_BIN if exist "C:\Program Files (x86)\VideoLAN\VLC\vlc.exe" set "VLC_BIN=C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"

if not defined VLC_BIN (
  echo VLC not found. Install VLC or edit this script with the correct path.
  exit /b 1
)

set "TARGET=tcp://127.0.0.1:24001"
set "WINDOW_TITLE=Hogak Transmit VLC"
if /I "%MODE%"=="probe" (
  set "TARGET=udp://@:23000"
  set "WINDOW_TITLE=Hogak Probe"
)
if /I "%MODE%"=="transmit-udp" (
  set "TARGET=udp://@:24000"
  set "WINDOW_TITLE=Hogak Transmit UDP"
)
if /I "%MODE%"=="transmit-tcp" (
  set "TARGET=tcp://127.0.0.1:24001"
  set "WINDOW_TITLE=Hogak Transmit VLC"
)

echo Opening %TARGET% with VLC low-latency options ^(network-caching=%CACHE_MS%ms^)
"%VLC_BIN%" ^
  --no-video-title-show ^
  --network-caching=%CACHE_MS% ^
  --live-caching=%CACHE_MS% ^
  --udp-caching=%CACHE_MS% ^
  --clock-jitter=0 ^
  --clock-synchro=0 ^
  --drop-late-frames ^
  --skip-frames ^
  --avcodec-hw=none ^
  --meta-title="%WINDOW_TITLE%" ^
  "%TARGET%"

exit /b %ERRORLEVEL%
