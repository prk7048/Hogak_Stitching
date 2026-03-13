@echo off
setlocal EnableExtensions

set "ROOT=%~dp0.."

if not defined PYTHON set "PYTHON=%ROOT%\.venv312\Scripts\python.exe"
if not defined LEFT_URL set "LEFT_URL=rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1^&subtype=0"
if not defined RIGHT_URL set "RIGHT_URL=rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1^&subtype=0"
if not defined PROBE_TARGET set "PROBE_TARGET=udp://127.0.0.1:23000?pkt_size=1316"
if not defined TRANSMIT_TARGET set "TRANSMIT_TARGET=udp://127.0.0.1:24000?pkt_size=1316"
if not defined VLC_TARGET set "VLC_TARGET=tcp://127.0.0.1:24001"
if not defined HOMOGRAPHY_FILE set "HOMOGRAPHY_FILE=%ROOT%\output\native\runtime_homography.json"
if not defined OUTPUT_STANDARD set "OUTPUT_STANDARD=realtime_hq_1080p"
if not defined HOGAK_VIEWER_BACKEND set "HOGAK_VIEWER_BACKEND=auto"
if not defined RTSP_TRANSPORT set "RTSP_TRANSPORT=udp"
if not defined INPUT_PIPE_FORMAT set "INPUT_PIPE_FORMAT=nv12"
if not defined INPUT_BUFFER_FRAMES set "INPUT_BUFFER_FRAMES=8"
if not defined RTSP_TIMEOUT_SEC set "RTSP_TIMEOUT_SEC=10"
if not defined RECONNECT_COOLDOWN_SEC set "RECONNECT_COOLDOWN_SEC=0.5"
if not defined PAIR_REUSE_MAX_AGE_MS set "PAIR_REUSE_MAX_AGE_MS=140"
if not defined PAIR_REUSE_MAX_CONSECUTIVE set "PAIR_REUSE_MAX_CONSECUTIVE=4"
if not defined SYNC_MANUAL_OFFSET_MS set "SYNC_MANUAL_OFFSET_MS=0"
if not defined OUTPUT_BITRATE set "OUTPUT_BITRATE=16M"
if not defined OUTPUT_PRESET set "OUTPUT_PRESET=p4"
if not defined TRANSMIT_CODEC set "TRANSMIT_CODEC=h264_nvenc"
if not defined STATUS_INTERVAL_SEC set "STATUS_INTERVAL_SEC=5"
if not defined VIEWER_TITLE set "VIEWER_TITLE=Hogak Probe Viewer"

if not exist "%PYTHON%" (
  echo Python venv not found: %PYTHON%
  exit /b 1
)

set "MODE_NO_VIEWER=0"
if /I "%~1"=="--no-viewer" (
  set "MODE_NO_VIEWER=1"
  shift
)

cd /d "%ROOT%"

if "%MODE_NO_VIEWER%"=="1" goto run_no_viewer

"%PYTHON%" -m stitching.cli native-runtime ^
  --left-rtsp "%LEFT_URL%" ^
  --right-rtsp "%RIGHT_URL%" ^
  --input-runtime ffmpeg-cuda ^
  --input-pipe-format %INPUT_PIPE_FORMAT% ^
  --rtsp-transport %RTSP_TRANSPORT% ^
  --input-buffer-frames %INPUT_BUFFER_FRAMES% ^
  --rtsp-timeout-sec %RTSP_TIMEOUT_SEC% ^
  --reconnect-cooldown-sec %RECONNECT_COOLDOWN_SEC% ^
  --sync-manual-offset-ms %SYNC_MANUAL_OFFSET_MS% ^
  --pair-reuse-max-age-ms %PAIR_REUSE_MAX_AGE_MS% ^
  --pair-reuse-max-consecutive %PAIR_REUSE_MAX_CONSECUTIVE% ^
  --probe-source standalone ^
  --probe-output-runtime ffmpeg ^
  --probe-output-target "%PROBE_TARGET%" ^
  --output-standard %OUTPUT_STANDARD% ^
  --transmit-output-runtime ffmpeg ^
  --transmit-output-target "%TRANSMIT_TARGET%" ^
  --transmit-output-codec %TRANSMIT_CODEC% ^
  --transmit-output-bitrate %OUTPUT_BITRATE% ^
  --transmit-output-preset %OUTPUT_PRESET% ^
  --transmit-output-debug-overlay ^
  --vlc-target "%VLC_TARGET%" ^
  --status-interval-sec %STATUS_INTERVAL_SEC% ^
  --homography-file "%HOMOGRAPHY_FILE%" ^
  --no-output-ui ^
  --viewer ^
  --viewer-backend %HOGAK_VIEWER_BACKEND% ^
  --viewer-title "%VIEWER_TITLE%" ^
  %*

exit /b %ERRORLEVEL%

:run_no_viewer
"%PYTHON%" -m stitching.cli native-runtime ^
  --left-rtsp "%LEFT_URL%" ^
  --right-rtsp "%RIGHT_URL%" ^
  --input-runtime ffmpeg-cuda ^
  --input-pipe-format %INPUT_PIPE_FORMAT% ^
  --rtsp-transport %RTSP_TRANSPORT% ^
  --input-buffer-frames %INPUT_BUFFER_FRAMES% ^
  --rtsp-timeout-sec %RTSP_TIMEOUT_SEC% ^
  --reconnect-cooldown-sec %RECONNECT_COOLDOWN_SEC% ^
  --sync-manual-offset-ms %SYNC_MANUAL_OFFSET_MS% ^
  --pair-reuse-max-age-ms %PAIR_REUSE_MAX_AGE_MS% ^
  --pair-reuse-max-consecutive %PAIR_REUSE_MAX_CONSECUTIVE% ^
  --probe-source disabled ^
  --output-standard %OUTPUT_STANDARD% ^
  --transmit-output-runtime ffmpeg ^
  --transmit-output-target "%TRANSMIT_TARGET%" ^
  --transmit-output-codec %TRANSMIT_CODEC% ^
  --transmit-output-bitrate %OUTPUT_BITRATE% ^
  --transmit-output-preset %OUTPUT_PRESET% ^
  --transmit-output-debug-overlay ^
  --vlc-target "%VLC_TARGET%" ^
  --status-interval-sec %STATUS_INTERVAL_SEC% ^
  --homography-file "%HOMOGRAPHY_FILE%" ^
  --no-output-ui ^
  %*

exit /b %ERRORLEVEL%
