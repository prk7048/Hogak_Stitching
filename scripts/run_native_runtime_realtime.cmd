@echo off
setlocal

set "OUTPUT_STANDARD=realtime_hq_1080p"
set "RTSP_TRANSPORT=udp"
set "INPUT_PIPE_FORMAT=nv12"
set "INPUT_BUFFER_FRAMES=8"
set "PAIR_REUSE_MAX_AGE_MS=140"
set "PAIR_REUSE_MAX_CONSECUTIVE=4"
set "SYNC_MANUAL_OFFSET_MS=0"
set "OUTPUT_BITRATE=16M"
set "OUTPUT_PRESET=p4"
set "RECONNECT_COOLDOWN_SEC=0.5"

call "%~dp0run_native_runtime_common.cmd" %*
exit /b %ERRORLEVEL%
