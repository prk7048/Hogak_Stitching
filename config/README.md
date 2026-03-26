# Config

이 디렉터리는 runtime site 설정과 profile override를 둔다.

- `runtime.json`
  - 기본 site config
  - 카메라 RTSP, homography 경로, probe/transmit target, 기본 cadence 등을 담는다
  - sync 기본값도 여기서 정한다
  - repo 기본 RTSP 값은 placeholder이므로 실행 전에 실제 현장 값으로 바꿔야 한다
- `runtime.local.json`
  - 현재 PC에서만 유지할 local override
  - git에는 올리지 않고, 있으면 `runtime.json` 위에 자동으로 덮어쓴다
- `profiles/*.json`
  - 기본 config 위에 덧씌우는 override
  - 장비/현장/운영 모드별 차이를 여기서 분리한다

적용 순서:

1. `config/runtime.json`
2. `config/runtime.local.json` (있을 때만)
3. `config/profiles/<name>.json` (`HOGAK_RUNTIME_PROFILE` 또는 `--runtime-profile <name>`를 썼을 때만)

예:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
```

```cmd
set HOGAK_RUNTIME_PROFILE=prod
python -m stitching.cli native-runtime
```

주요 sync 키:

- `sync_time_source`
  - 기본값은 `pts-offset-auto`
  - 선택 가능: `pts-offset-auto`, `pts-offset-manual`, `pts-offset-hybrid`, `arrival`, `wallclock`
- `sync_manual_offset_ms`
  - manual/hybrid에서 right stream에 더하는 offset
- `sync_auto_offset_window_sec`
- `sync_auto_offset_max_search_ms`
- `sync_recalibration_interval_sec`
- `sync_recalibration_trigger_skew_ms`
- `sync_recalibration_trigger_wait_ratio`
- `sync_auto_offset_confidence_min`

운영 권장:

- 기본은 `pts-offset-auto`
- 현장 offset이 고정돼 있으면 `pts-offset-manual`
- auto 실패 시 manual까지 같이 준비하려면 `pts-offset-hybrid`
- `wallclock`은 기본 운영이 아니라 진단/비교용

현재 auto sync 해석:

- source PTS가 있으면 기본 pair 시간축은 `stream_pts_offset`
- 기본 가설은 `0ms`
- auto estimator는 강한 증거가 있을 때만 비영 offset으로 이동
- 재보정은 작은 step으로 천천히 반영

현재 기본 튜닝값:

- `sync_auto_offset_window_sec=4.0`
- `sync_auto_offset_max_search_ms=500.0`
- `sync_recalibration_interval_sec=60.0`
- `sync_recalibration_trigger_skew_ms=45.0`
- `sync_recalibration_trigger_wait_ratio=0.50`
- `sync_auto_offset_confidence_min=0.85`

주요 distortion 키:

- `distortion_mode`
  - 기본값 `runtime-lines`
  - 선택 가능: `off`, `runtime-lines`
- `use_saved_distortion`
  - interactive runtime 시작 UI에서 저장된 좌/우 distortion file을 재사용할지 여부
  - headless runtime / `native-calibrate`에서는 saved distortion fallback 사용 여부
- `distortion_auto_save`
  - 현재 interactive manual workflow는 confirm 시 항상 저장한다
  - 이 필드는 shared config / CLI 호환성용으로 유지된다
- `left_distortion_file`
- `right_distortion_file`

운영 규칙:

- saved distortion file은 homography file과 분리 저장한다
- interactive runtime은 기본적으로 manual line selection을 먼저 수행하고, 사용자가 체크할 때만 saved distortion을 재사용한다
- distortion correction은 `undistorted` 기준으로 다시 만든 homography와 같이 써야 한다
- 기존 raw homography 파일이면 runtime은 distortion file이 있어도 실제 remap을 끈다
