# Hogak Stitching

두 개의 RTSP 카메라 입력을 받아 하나의 stitched stream으로 실시간 송출하는 프로젝트다.

현재 구조는 `Python control plane + C++ native runtime`이다.

- Python:
  - calibration UI
  - runtime launch/control
  - monitor/dashboard
  - final encoded output probe viewer
- C++ native runtime:
  - RTSP input
  - pair/sync
  - GPU warp/blend
  - encode/output stream

현재 기준 메인 경로는 Python에서 직접 스티칭하는 방식이 아니라,
Python이 calibration과 운영 제어를 맡고 C++ runtime이 실제 stitched stream을 만드는 방식이다.

상세 판단 문서는 [`reports/README.md`](/c:/Users/Pixellot/Hogak_Stitching/reports/README.md)를 보면 된다.

## Current Main Path

현재 운영 기준의 메인 경로는 아래 둘이다.

1. `python -m stitching.cli native-calibrate`
2. `python -m stitching.cli native-runtime`

## Quick Start

### 1. Install

```cmd
python -m pip install -r requirements.txt
```

### 2. Build Native Runtime

```cmd
copy native_runtime\CMakeUserPresets.example.json native_runtime\CMakeUserPresets.json
cmake --preset windows-release
cmake --build --preset build-windows-release
```

다른 Windows 머신에서 빌드할 때는 `native_runtime/CMakeUserPresets.json`에 로컬 `OpenCV_DIR`, `HOGAK_FFMPEG_DEV_ROOT`, `CUDAToolkit_ROOT`를 넣는 방식이 기본이다.
공용 preset은 특정 개발자 PC 경로를 직접 들고 있지 않고, repo 안 `.third_party`는 fallback으로만 사용한다.
로컬 preset 이름을 따로 쓰고 싶으면 `HOGAK_CMAKE_CONFIGURE_PRESET`, `HOGAK_CMAKE_BUILD_PRESET`를 지정한 뒤 `cmake --preset <preset>`와 `cmake --build --preset <preset>`를 쓰면 된다.

### 2-1. Check Supported Runtime Environment

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer --duration-sec 3 --monitor-mode compact
```

현재 머신에서 runtime이 바로 뜨는지, `gpu-direct` 의존성이 준비됐는지 빠르게 확인할 수 있다.

### 3. Calibrate

가장 간단한 실행:

```cmd
python -m stitching.cli native-calibrate
```

현재 calibration 기본 흐름:

1. 좌/우 대표 프레임 표시
2. 필요하면 overlap guide를 참고해 대응점 선택
3. `COMPLETE`
4. 실제 inlier match / stitched preview 검토
5. `CONFIRM`
6. homography 저장
7. main runtime 자동 실행

즉 calibration 성공 후 바로 runtime까지 이어진다.

보정만 하고 종료하려면:

```cmd
python -m stitching.cli native-calibrate --calibration-only
```

CLI 직접 실행도 가능하다.

```cmd
python -m stitching.cli native-calibrate
```

## Runtime

현재 사이트별 기본값은 코드에 직접 박혀 있지 않고 [config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)에서 읽는다.

- 카메라 RTSP 주소
- homography 파일 경로
- probe/transmit UDP target
- 기본 output cadence / transport / input buffer

현재 runtime homography는 [data/runtime_homography.json](/c:/Users/Pixellot/Hogak_Stitching/data/runtime_homography.json)에 두고,
calibration preview 같은 재생성 가능한 산출물만 [output](/c:/Users/Pixellot/Hogak_Stitching/output)에 남긴다.

다른 현장이나 다른 PC에서 돌릴 때는 코드를 수정하지 말고 이 파일만 바꾸면 된다.
필요하면 `HOGAK_RUNTIME_CONFIG` 환경변수로 다른 설정 파일 경로를 지정할 수도 있다.
기본 config 위에 운영 profile을 덧씌우고 싶으면 [config/profiles](/c:/Users/Pixellot/Hogak_Stitching/config/profiles)을 쓰면 된다.

예:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
```

```cmd
set HOGAK_RUNTIME_PROFILE=prod
python -m stitching.cli native-runtime
```

기본 운영 경로:

```cmd
python -m stitching.cli native-runtime
```

viewer 없이 monitor만 보려면:

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer
```

현재 기본 운영 진입점은 `python -m stitching.cli native-runtime`다.
`--no-output-ui`를 주면 시작 UI를 건너뛰고 바로 실행한다.

현재 post-stitch 기본 구조는 아래처럼 정리한다.

```text
viewer on:
stitched frame -> standalone probe encode -> local UDP probe -> viewer/debug receive
               -> transmit encode -> external target

viewer off:
stitched frame -> transmit encode -> external target
```

- `probe`: viewer가 켜져 있을 때만 쓰는 standalone local debug stream이다.
- `transmit`: 실제 외부 송출 경로
- viewer는 raw preview가 아니라 `probe`를 다시 받아 보여준다
- 기본 runtime 실행은 현재 `transmit`에 debug overlay를 넣는다. `frame`, `seq`, `reuse`, `pair_age`가 보여서 24000 화면이 진짜 멈춘 건지 반복 프레임인지 바로 구분할 수 있다.
- viewer backend는 `ffplay`와 `opencv`만 지원한다. 예전 VLC low-latency 경로는 제거했다.

realtime 우선 프리셋:

```cmd
python -m stitching.cli native-runtime --output-standard realtime_gpu_1080p
```

25fps 카메라 profile:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
```

## Deployment Bundle

다른 Windows 머신에 전달할 때는 `config/`, `data/`, `stitching/`, `native_runtime/build/windows-release/Release/`를 함께 묶는 수동 번들 기준으로 운영한다.

현재 기본 운영값 방향:

- probe source: viewer를 켜면 `standalone`, `--no-viewer`면 `disabled`
- probe output: local UDP loopback
- transmit output: 기본 활성화, `gpu-direct`
- output size baseline: stitched output size 그대로
- codec: `h264_nvenc`
- pair mode: realtime 쪽이 기본

지원 preset은 realtime 경로만 유지한다.

예:

```cmd
python -m stitching.cli native-runtime --left-rtsp "..." --right-rtsp "..." --output-standard realtime_hq_1080p
python -m stitching.cli native-runtime --left-rtsp "..." --right-rtsp "..." --output-standard realtime_gpu_1080p
python -m stitching.cli native-runtime --left-rtsp "..." --right-rtsp "..." --no-output-ui --no-viewer --transmit-output-runtime gpu-direct --transmit-output-target "udp://10.0.0.20:5000?pkt_size=1316" --transmit-output-width 0 --transmit-output-height 0
```

현재 지원 preset:

- `realtime_hq_1080p`
- `realtime_gpu_1080p`

## What To Watch

monitor에서 우선 볼 값:

- `probe_fps`, `transmit_fps`
- `left_fps`, `right_fps`
- `left_age_ms`, `right_age_ms`
- `left_motion_mean`, `right_motion_mean`
- `probe active`, `transmit active`
- `viewer`

해석:

- `probe_fps`는 viewer를 켠 상태에서 standalone local debug stream이 실제로 흘러가는지 보여준다.
- `transmit_fps`는 외부 송출 경로가 실제로 흘러가는지 보여준다.
- `actual_fps`와 `transmit_fps`가 비슷하면 본선 송출이 stitched 생산 속도를 잘 따라간다는 뜻이다.
- `age_ms`가 커지면 해당 입력이 멈추거나 지연된 상태다.
- `motion_mean`이 낮고 age는 낮다면, 프레임은 오지만 내용이 얼어 있는 상태일 수 있다.
- `viewer`는 raw snapshot이 아니라 post-encode local debug receive 기준이다.

## Calibration Notes

현재 calibration 원칙:

- 사용자는 대응점을 `0..n`개까지 줄 수 있다
- auto baseline은 항상 먼저 계산한다
- 수동 점은 seed로만 사용한다
- assisted 결과가 baseline보다 나쁘면 버린다
- 최종 저장은 더 좋은 candidate만 사용한다

즉 수동 입력은 “강제 정답”이 아니라 “더 좋은 매칭을 돕는 힌트”다.

딥러닝 backend도 calibration 단계에만 후보로 들어간다.

- `--match-backend deep`
- `--deep-backend auto|lightglue|loftr`

현재 동작:

- `auto`: deep backend가 없으면 classic으로 fallback
- `deep`: deep backend가 실제로 없으면 명시적으로 실패

즉 deep matcher는 baseline auto를 깨는 기본 경로가 아니라, 더 좋을 때만 채택되는 추가 후보다.

## Common Errors

- `OVERLAP_LOW`: calibration match가 너무 적다
- `HOMOGRAPHY_FAIL`: homography 또는 geometry 품질이 부족하다
- `ENCODE_FAIL`: output encode path 실패
- `INTERNAL_ERROR`: 내부 처리 실패

## Status

현재 상태를 짧게 요약하면:

- native runtime main path는 이미 동작한다
- calibration UX와 operator flow는 direct Python entrypoint 기준으로 정리됐다
- config/profile/data 구조도 현재 운영 경로 기준으로 정리됐다
- 다음 핵심은 실제 서비스 기준에서 long-run 운영 검증과 source cadence 분리 진단이다

즉 지금 phase는 구조를 새로 만드는 단계보다 운영 baseline을 마감하는 단계에 가깝다.
