## Summary

현재 프로젝트의 메인 runtime 경로는 이미 `Python control plane + C++ native runtime`으로 정착했다.

핵심 상태를 한 줄로 요약하면 다음과 같다.

> 본선 transmit 경로는 `gpu-direct` 기준으로 정리됐고, 지금 남은 핵심은 input/source cadence를 안정화해서 `strict fresh 30fps` live baseline을 닫는 일이다.

## Current Main Path

현재 운영 기준 메인 경로는 아래 둘이다.

1. `python -m stitching.cli native-calibrate`
2. `python -m stitching.cli native-runtime`

동일한 baseline을 직접 실행할 때는 아래를 쓴다.

- `python -m stitching.cli native-runtime`
- `python -m stitching.cli native-runtime --output-standard realtime_gpu_1080p`
- `python -m stitching.cli native-runtime --output-standard realtime_gpu_1080p --sync-pair-mode service --no-allow-frame-reuse`

## What Is Implemented

### Native Runtime

- RTSP input reader
- `ffmpeg-cuda` input baseline
- `service` pair scheduler
- GPU warp / GPU blend
- `gpu-direct` production transmit writer
- JSON metrics / compact dashboard monitor

### Python Control Plane

- calibration launcher and confirm flow
- runtime launcher
- compact/dashboard monitor
- preset selection UI
- probe viewer launch (`ffplay` / `opencv`)

### Current Operating Defaults

- input runtime: `ffmpeg-cuda`
- input pipe format: `nv12`
- input buffer: `8`
- probe source: viewer on이면 `standalone`, `--no-viewer`면 `disabled`
- transmit runtime: `gpu-direct`
- transmit size: stitched output size 그대로
- supported presets:
  - `realtime_1080p`
  - `realtime_hq_1080p`
  - `realtime_gpu_1080p`
  - `realtime_hq_1080p_strict`

## What Has Been Confirmed

현재 코드 기준으로 확인된 동작:

- 좌/우 RTSP 입력 수신
- calibration homography load
- GPU warp/blend path 동작
- stitched size 그대로의 transmit output 송출
- Python UI 경로와 `.cmd` baseline 경로 정렬
- `gpu-direct` transmit 실제 동작
- monitor에서 input/stitch/transmit/system 상태 확인
- `ffplay`/`opencv` viewer fallback 동작

즉 end-to-end 운영 경로 자체는 이미 살아 있고, 예전처럼 구조가 흔들리는 단계는 지났다.

## Current Constraints

지금 운영 판단에서 중요한 제약은 아래다.

1. 현재 live 카메라는 사실상 `30fps`급 입력이다.
2. 그래서 현재 현실적인 live 목표는 `strict fresh 30fps`다.
3. `60fps fresh stitch`는 입력이 `2x60`으로 바뀐 뒤 다시 여는 목표다.

## Current Risks

아직 남아 있는 리스크는 다음이다.

1. right-side input cadence/source jitter
2. long-run strict fresh `30fps` 미검증
3. OpenCV CUDA build에서 `NV12 -> BGR` GPU 변환 미지원
4. source 문제와 code 문제를 완전히 분리 진단하지 않은 상태

핵심은 더 이상 viewer나 output writer가 아니라,
`input/pair/source` 쪽 변동성이다.

## Current Best Interpretation

지금까지의 측정을 종합하면:

- output path는 많이 정리됐다
- GPU는 아직 꽉 차지 않는다
- pair scheduler도 예전보다 좋아졌다
- 하지만 fresh pair 공급은 아직 source cadence 영향을 크게 받는다

즉 현재 병목은 아래처럼 읽는 게 맞다.

> scheduler가 frame을 고르는 방식 자체보다, 특히 right-side 입력이 fresh frame을 일정하게 못 주는 문제가 더 크다.

## Progress Estimate

실무적으로 보면 현재 진척은 이 정도로 보는 게 맞다.

- 구조 전환: `90%+`
- 운영 baseline 정리: `75~80%`
- strict fresh `30fps` 검증: 진행 중

즉 지금 phase는 "새 엔진을 만드는 단계"가 아니라,
"현재 운영 baseline을 닫고 다음 단계로 넘길 기준을 만드는 단계"다.
