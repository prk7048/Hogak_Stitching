# Operator Acceptance

이 문서는 현재 유지 중인 제품 표면을 실제 운영 환경에서 확인하기 위한 수동 acceptance runbook 이다.

## Prerequisites

- Windows machine
- two RTSP cameras with site-local credentials configured
- native prerequisites checked with `native_runtime\bootstrap_native_runtime.ps1`
- React bundle built once with `cd frontend && npm install && npm run build`

실제 RTSP 값은 `config/runtime.local.json` 에 넣는다. repo의 `config/runtime.json` 은 placeholder 값만 유지한다.

## Build and Startup

```cmd
python -m pip install -r requirements.txt
copy native_runtime\CMakeUserPresets.example.json native_runtime\CMakeUserPresets.json
cmake --preset windows-release
cmake --build --preset build-windows-release
python -m stitching.cli operator-server
```

기본 operator surface:

- UI: `http://127.0.0.1:8088/`
- API: `GET /api/project/state`, `POST /api/project/start`, `POST /api/project/stop`

## Acceptance Steps

1. Open `http://127.0.0.1:8088/`.
2. Confirm the page shows the single project surface, not legacy calibration or bakeoff views.
3. Call `GET /api/project/state` and confirm the response includes `lifecycle_state`, `phase`, `geometry`, `runtime`, `output`, `zero_copy`.
4. Press `Start Project` or call `POST /api/project/start`.
5. Confirm the page progresses through input check, geometry refresh or reuse, runtime prepare, output start, and live output confirmation.
6. Open the external player on `udp://@:24000`.
7. Confirm live stitched output is visible.
8. Press `Stop Project` or call `POST /api/project/stop`.
9. Confirm the page returns to the idle state.

## Things To Confirm

- `runtime.active_model` reports `virtual-center-rectilinear-rigid`
- `geometry.residual_model` reports `rigid`
- `geometry.fallback_used` is `false` during the normal path
- `geometry.launch_ready` is `true` before runtime start completes
- `output.receive_uri` or `output.target` matches the external player target
- `runtime.gpu_path_mode` settles on `native-nvenc-direct` when the target path is healthy

## Failure Notes

- If start is blocked, the error should tell the operator to configure real RTSP values in `config/runtime.local.json`.
- If the React bundle is missing, the backend fallback page should still list only the three supported project APIs.
- If a non-default artifact is active, the runtime state must show that fallback usage explicitly.
