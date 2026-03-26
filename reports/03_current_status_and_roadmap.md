## Summary

현재 프로젝트의 메인 runtime 경로는 `Python control plane + C++ native runtime`으로 정착했다.

핵심 상태를 한 줄로 요약하면 다음과 같다.

> 본선 transmit 경로는 `gpu-direct` 기준으로 정리됐고, 지금 남은 핵심은 input/source cadence를 안정화해서 `strict fresh 30fps` live baseline을 닫는 일이다.

## Current Main Path

현재 운영 기준 메인 경로는 아래 둘이다.

1. `python -m stitching.cli native-calibrate`
2. `python -m stitching.cli native-runtime`

자주 쓰는 실행 예시는 아래다.

- `python -m stitching.cli native-runtime`
- `python -m stitching.cli native-runtime --no-output-ui --no-viewer`
- `python -m stitching.cli --runtime-profile camera25 native-runtime`

## Current Baseline

현재 문서와 코드가 같이 가리키는 baseline은 다음이다.

- input runtime: `ffmpeg-cuda`
- input reader model: in-process `libav` demux/decode
- input pipe format: `nv12`
- input buffer: `8`
- pair mode: `service`
- transmit runtime: `gpu-direct`
- output codec baseline: `h264_nvenc`
- transmit size: stitched output size 그대로
- timing model:
  - arrival metrics는 계속 운영 기준
  - source timestamp는 병행 수집
  - 기본 pairing은 `pts-offset-auto`
  - auto 실패 시 `fallback-arrival`
  - manual offset은 선택 가능
  - `wallclock`은 진단/명시적 opt-in 전용
- supported presets:
  - `realtime_hq_1080p`
  - `realtime_gpu_1080p`

## What Is Already Working

현재 코드 기준으로 확인된 동작:

- 좌/우 RTSP 입력 수신
- calibration homography load
- GPU warp / GPU blend path 동작
- `gpu-direct` transmit 동작
- Python UI 경로와 direct Python headless 경로 정렬
- monitor에서 input/stitch/transmit/system 상태 확인
- source timing metrics(`left_source_age_ms`, `right_source_age_ms`, `pair_source_skew_ms_mean`, `source_time_mode`) 노출
- `ffplay` / `opencv` viewer fallback 동작

즉 end-to-end 운영 경로 자체는 이미 살아 있고, 구조를 다시 갈아엎는 단계는 지났다.

## Current Constraints

지금 운영 판단에서 중요한 제약은 아래다.

1. 현재 live 카메라는 사실상 `30fps`급 입력이다.
2. 그래서 현재 현실적인 live 목표는 `strict fresh 30fps`다.
3. `60fps fresh stitch`는 입력이 `2x60`으로 바뀐 뒤 다시 여는 목표다.

## Current Risks

아직 남아 있는 리스크는 다음이다.

1. right-side input cadence/source jitter
2. source wallclock이 모든 환경에서 cross-camera comparable하지 않을 수 있음
3. motion이 약한 장면에서 auto offset confidence가 떨어질 수 있음
4. long-run strict fresh `30fps` 미검증
5. OpenCV CUDA build에서 `NV12 -> BGR` GPU 변환 미지원
6. source 문제와 code 문제를 완전히 분리 진단하지 않은 상태

핵심은 더 이상 viewer나 output writer가 아니라 `input/pair/source` 쪽 변동성이다.

## Best Current Interpretation

지금까지의 측정을 종합하면:

- output path는 많이 정리됐다
- GPU는 아직 꽉 차지 않는다
- pair scheduler도 예전보다 좋아졌다
- 하지만 fresh pair 공급은 아직 source cadence 영향을 크게 받는다

즉 현재 병목은 아래처럼 읽는 게 맞다.

> scheduler가 frame을 고르는 방식 자체보다, 특히 right-side 입력이 fresh frame을 일정하게 못 주는 문제가 더 크다.

## Immediate Goal

지금 당장의 목표는 `strict fresh 30fps live baseline`을 운영 기준으로 닫는 것이다.

뜻:

- 현재 `30fps`급 카메라 입력 조건에서
- `gpu-direct` transmit baseline을 유지하고
- input/source 흔들림이 실제 병목인지 분리 확인하고
- source timestamp가 있는 환경에서는 arrival보다 더 나은 pair 선택이 가능한지 확인하고
- 기본 `pts-offset-auto`가 실제 현장 offset을 안정적으로 따라가는지 확인하고
- 장시간 실행에서도 운영 가능한지 검증하는 상태

## Priority Order

### 1. Source / Cadence Diagnosis

필요한 것:

- 좌/우 카메라를 바꿔서 문제가 `right 자리`를 따라가는지 확인
- 좌/우 RTSP를 단독으로 받아 cadence/gap을 비교
- 가능하면 Wi-Fi/UDP 영향과 source 자체 영향 분리
- 카메라 fps / GOP / bitrate / transport 조건 재확인

### 2. Strict Fresh 30 Long-Run Validation

필요한 것:

- `ffmpeg-cuda + nv12 + input_buffer_frames=8 + gpu-direct`
- viewer off / probe disabled baseline
- 30분 이상 long-run
- `active_stitch_fps`, `waiting_ratio`, `transmit_to_stitched_ratio`, read/restart 지표 기록
- `pair_skew_ms_mean`, `pair_source_skew_ms_mean`, `source_time_mode` 함께 기록

### 3. Limited Code Follow-Up Only If Needed

source 문제가 아니라 code 병목으로 확인된 경우에만:

- reader cadence 계측 추가
- queue/pair 정책 미세조정
- input boundary 최적화 추가

원칙:

- 이미 효과가 작았던 queue 미세조정에 오래 머물지 않는다

### 4. Documentation Freeze

필요한 것:

- 루트 README 실행 방법 고정
- reports 현재 상태 반영
- viewer/backend/preset 설명 정리

## Done Criteria For This Stage

다음 조건이 맞으면 현재 단계를 닫을 수 있다.

1. 메인 실행 경로가 문서와 일치한다
2. `strict fresh 30fps` long-run 결과가 충분히 해석 가능하다
3. arrival/source 지표를 같이 보고 source 문제인지 code 문제인지 분리 판단이 가능하다
4. 운영 baseline과 장기 60fps 계획이 문서에서 구분된다

## What Comes After This Stage

이 단계가 끝나면 다음 갈래는 둘 중 하나다.

### A. Source-Limited Case

source/camera cadence가 주병목으로 확인되면:

- camera/network 조건 개선을 우선한다
- code 쪽은 현재 baseline 유지
- 60fps fresh 목표는 `2x60` 입력 확보 뒤 다시 연다

### B. Code-Limited Case

source보다 code path 병목이 더 크다고 확인되면:

- input boundary 재구성
- OpenCV 밖 GPU path 검토
- `future 2x60 -> 60fps` 본선 작업 재개

## Progress Estimate

실무적으로 보면 현재 진척은 이 정도로 보는 게 맞다.

- 구조 전환: `90%+`
- 운영 baseline 정리: `75~80%`
- strict fresh `30fps` 검증: 진행 중

즉 지금 phase는 새 엔진을 만드는 단계가 아니라, 현재 운영 baseline을 닫고 다음 단계로 넘길 기준을 만드는 단계다.
