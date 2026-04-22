# Config

이 디렉터리는 runtime site 설정과 profile override를 둔다.

- `runtime.json`
  - 체크인되는 기본 site config
  - 카메라 RTSP placeholder, 기본 경로, probe/transmit target, cadence, sync 기본값을 담는다
- `runtime.local.json`
  - 현재 PC에서만 유지할 local override
  - git에는 올리지 않고, 있으면 `runtime.json` 위에 자동으로 덮어쓴다
- `profiles/*.json`
  - 기본 config 위에 덧씌우는 named override
  - 장비/현장/운영 모드별 차이를 여기서 분리한다

적용 순서:

1. `config/runtime.json`
2. `config/runtime.local.json` (있을 때만)
3. `config/profiles/<name>.json` (`HOGAK_RUNTIME_PROFILE` 또는 `--runtime-profile <name>`를 썼을 때만)

예:

```cmd
python -m stitching.cli --runtime-profile camera25 operator-server
```

```cmd
set HOGAK_RUNTIME_PROFILE=prod
python -m stitching.cli operator-server
```

운영 규칙:

- repo의 `runtime.json` 은 placeholder RTSP 값만 유지한다
- 실제 현장 RTSP 값은 `runtime.local.json` 이나 `HOGAK_LEFT_RTSP` / `HOGAK_RIGHT_RTSP` 에 둔다
- `operator-server` 와 `mesh-refresh` 는 모두 active rigid runtime artifact 흐름을 기준으로 동작한다
- 웹 표면은 단일 페이지에서 `Project state -> Start Project -> Stop Project` 만 노출한다
- `data/runtime_calibration_inliers.json` 은 preview/debug overlay 용이며 runtime artifact truth 자체는 아니다

주요 sync 키:

- `sync_time_source`
  - 기본값은 `pts-offset-auto`
  - 선택 가능: `pts-offset-auto`, `pts-offset-manual`, `pts-offset-hybrid`, `arrival`, `wallclock`
- `sync_manual_offset_ms`
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
- `wallclock` 은 기본 운영이 아니라 진단/비교용
