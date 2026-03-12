@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv312\Scripts\python.exe"
set "LEFT_URL=rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1^&subtype=0"
set "RIGHT_URL=rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1^&subtype=0"
set "OUTPUT_TARGET=udp://127.0.0.1:23000?pkt_size=1316"
set "VIEWER_TARGET=udp://127.0.0.1:23000"
set "HOMOGRAPHY_FILE=%ROOT%\output\native\runtime_homography.json"

if not exist "%PYTHON%" (
  echo Python venv not found: %PYTHON%
  exit /b 1
)

cd /d "%ROOT%"

if /I "%~1"=="--no-viewer" goto run_no_viewer

"%PYTHON%" -m stitching.cli native-runtime ^
  --left-rtsp "%LEFT_URL%" ^
  --right-rtsp "%RIGHT_URL%" ^
  --input-runtime ffmpeg-cuda ^
  --rtsp-transport tcp ^
  --rtsp-timeout-sec 10 ^
  --reconnect-cooldown-sec 1 ^
  --sync-pair-mode latest ^
  --sync-match-max-delta-ms 60 ^
  --sync-manual-offset-ms 0 ^
  --stitch-output-scale 0.25 ^
  --output-runtime ffmpeg ^
  --output-target "%OUTPUT_TARGET%" ^
  --output-width 1920 ^
  --output-height 1080 ^
  --output-codec h264_nvenc ^
  --output-bitrate 6M ^
  --output-preset p1 ^
  --status-interval-sec 5 ^
  --homography-file "%HOMOGRAPHY_FILE%" ^
  --viewer ^
  --viewer-target "%VIEWER_TARGET%" ^
  --viewer-title "Hogak Final Stream"

exit /b %ERRORLEVEL%

:run_no_viewer
"%PYTHON%" -m stitching.cli native-runtime ^
  --left-rtsp "%LEFT_URL%" ^
  --right-rtsp "%RIGHT_URL%" ^
  --input-runtime ffmpeg-cuda ^
  --rtsp-transport tcp ^
  --rtsp-timeout-sec 10 ^
  --reconnect-cooldown-sec 1 ^
  --sync-pair-mode latest ^
  --sync-match-max-delta-ms 60 ^
  --sync-manual-offset-ms 0 ^
  --stitch-output-scale 0.25 ^
  --output-runtime ffmpeg ^
  --output-target "%OUTPUT_TARGET%" ^
  --output-width 1920 ^
  --output-height 1080 ^
  --output-codec h264_nvenc ^
  --output-bitrate 6M ^
  --output-preset p1 ^
  --status-interval-sec 5 ^
  --homography-file "%HOMOGRAPHY_FILE%"

exit /b %ERRORLEVEL%
