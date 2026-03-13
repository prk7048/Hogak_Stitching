@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv312\Scripts\python.exe"
set "LEFT_URL=rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1^&subtype=0"
set "RIGHT_URL=rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1^&subtype=0"
set "PROBE_TARGET=udp://127.0.0.1:23000?pkt_size=1316"
set "TRANSMIT_TARGET=udp://127.0.0.1:24000?pkt_size=1316"
set "HOMOGRAPHY_FILE=%ROOT%\output\native\runtime_homography.json"
if not defined OUTPUT_STANDARD set "OUTPUT_STANDARD=realtime_hq_1080p_strict"
if not defined HOGAK_VIEWER_BACKEND set "HOGAK_VIEWER_BACKEND=auto"
set "RTSP_TRANSPORT=tcp"
set "INPUT_BUFFER_FRAMES=6"
set "PAIR_REUSE_MAX_AGE_MS=90"
set "PAIR_REUSE_MAX_CONSECUTIVE=2"

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
  --rtsp-transport %RTSP_TRANSPORT% ^
  --input-buffer-frames %INPUT_BUFFER_FRAMES% ^
  --rtsp-timeout-sec 10 ^
  --reconnect-cooldown-sec 1 ^
  --sync-manual-offset-ms 0 ^
  --pair-reuse-max-age-ms %PAIR_REUSE_MAX_AGE_MS% ^
  --pair-reuse-max-consecutive %PAIR_REUSE_MAX_CONSECUTIVE% ^
  --probe-source standalone ^
  --probe-output-runtime ffmpeg ^
  --probe-output-target "%PROBE_TARGET%" ^
  --output-standard %OUTPUT_STANDARD% ^
  --transmit-output-runtime ffmpeg ^
  --transmit-output-target "%TRANSMIT_TARGET%" ^
  --transmit-output-codec h264_nvenc ^
  --transmit-output-bitrate 12M ^
  --transmit-output-preset p4 ^
  --transmit-output-debug-overlay ^
  --status-interval-sec 5 ^
  --homography-file "%HOMOGRAPHY_FILE%" ^
  --no-output-ui ^
  --viewer ^
  --viewer-backend %HOGAK_VIEWER_BACKEND% ^
  --viewer-title "Hogak Probe Viewer"

exit /b %ERRORLEVEL%

:run_no_viewer
"%PYTHON%" -m stitching.cli native-runtime ^
  --left-rtsp "%LEFT_URL%" ^
  --right-rtsp "%RIGHT_URL%" ^
  --input-runtime ffmpeg-cuda ^
  --rtsp-transport %RTSP_TRANSPORT% ^
  --input-buffer-frames %INPUT_BUFFER_FRAMES% ^
  --rtsp-timeout-sec 10 ^
  --reconnect-cooldown-sec 1 ^
  --sync-manual-offset-ms 0 ^
  --pair-reuse-max-age-ms %PAIR_REUSE_MAX_AGE_MS% ^
  --pair-reuse-max-consecutive %PAIR_REUSE_MAX_CONSECUTIVE% ^
  --probe-source disabled ^
  --output-standard %OUTPUT_STANDARD% ^
  --transmit-output-runtime ffmpeg ^
  --transmit-output-target "%TRANSMIT_TARGET%" ^
  --transmit-output-codec h264_nvenc ^
  --transmit-output-bitrate 12M ^
  --transmit-output-preset p4 ^
  --transmit-output-debug-overlay ^
  --status-interval-sec 5 ^
  --homography-file "%HOMOGRAPHY_FILE%" ^
  --no-output-ui

exit /b %ERRORLEVEL%
