# 60fps Service Pipeline Plan

## Why This Document Exists

이 문서는 현재 프로젝트의 다음 최우선 목표를 고정하기 위한 기준 문서다.

현재 목표는 단순한 `1080p 30fps demo`가 아니라 아래와 같다.

- 좌/우 입력이 `60fps`로 들어올 수 있다
- stitched 결과도 `60fps` 서비스 기준으로 유지한다
- probe/viewer는 보조 기능이고, 본선 transmit 성능을 방해하지 않는다

즉 이 문서는 "남은 튜닝 목록"이 아니라,
"60fps 서비스 기준으로 파이프라인을 다시 설계하고 단계적으로 옮기기 위한 실행 계획"이다.

## Current Decision

다음 판단을 기준으로 진행한다.

1. `25fps` 또는 `30fps 미만`으로 타협하지 않는다.
2. 최종 목표는 `2x60 input -> 60fps stitch -> 60fps transmit`이다.
3. 현재 구조는 GPU 성능 부족보다 `GPU 앞뒤 CPU 경계`가 더 큰 문제다.
4. 따라서 지금 단계는 단순 preset 튜닝이 아니라 `main transmit path re-architecture` 단계다.

## What We Confirmed

최근 점검에서 확인된 사실은 다음과 같다.

### 1. GPU가 이미 꽉 찬 상태는 아니다

- `realtime_hq_1080p` 진단에서 GPU 사용률은 대략 `25%` 수준이었다.
- 같은 조건에서 실제 stitched fps는 평균 `24fps` 정도였다.
- 즉 GPU가 남아 있는데도 stitched fps가 기대만큼 올라가지 않았다.

### 2. aggressive preset은 오히려 더 느려졌다

- `realtime_gpu_1080p` 진단에서 GPU 사용률은 여전히 `22~25%` 수준이었다.
- 하지만 실제 stitched fps는 평균 `9~10fps` 수준까지 떨어졌다.
- 출력은 계속 cadence를 유지하려고 해서 같은 프레임을 여러 번 반복했다.

### 3. output writer가 프레임을 많이 버린 것은 아니다

- probe/transmit dropped count는 진단에서 거의 `0`이었다.
- 즉 "output writer가 감당 못 해서 버린다"기보다
  "앞단에서 새 stitched frame이 충분히 빨리 생성되지 않는다"가 더 맞다.

### 4. 남은 큰 병목은 CPU 경계와 프로세스 경계다

현재 메인 경로는 사실상 다음과 같다.

`RTSP -> ffmpeg rawvideo -> CPU frame copy -> GPU stitch -> CPU download -> CPU copy -> external ffmpeg rawvideo stdin -> NVENC -> UDP`

이 구조에서는 GPU가 남아 있어도 전체 fps가 쉽게 올라가지 않는다.

## What The Middle-Stage Problem Actually Is

쉽게 말하면 GPU가 만든 영상을 "바로 보내는" 구조가 아니라,
중간에 다시 CPU로 가져와서 여러 번 복사한 뒤 다른 프로세스에 넘기는 구조다.

### Input Side

현재 입력 경로는:

- `ffmpeg` 외부 프로세스가 RTSP를 읽는다
- `rawvideo bgr24`로 CPU 메모리에 내린다
- native runtime이 그 프레임을 다시 버퍼에 복사한다

관련 코드:

- `-pix_fmt bgr24 -f rawvideo -`: `native_runtime/src/input/ffmpeg_rtsp_reader.cpp`
- frame copy: `native_runtime/src/input/ffmpeg_rtsp_reader.cpp`

즉 입력 시작부터 이미 CPU raw frame copy 구조다.

### Pair / Sync Side

현재 엔진은 다음 상태를 반복한다.

- `waiting for both streams`
- `waiting sync pair`
- `waiting next frame`
- `waiting paired fresh frame`

관련 코드:

- `native_runtime/src/engine/stitch_engine.cpp`

즉 GPU가 놀고 있는 시간 중 일부는 "프레임 자체가 아직 준비되지 않은 시간"이다.

### Stitch Side

stitch 자체는 CUDA path를 쓰지만,
결과를 다시 CPU로 내려오는 지점이 있다.

관련 코드:

- `gpu_output_canvas_.download(...)`
- `gpu_stitched_.download(...)`

즉 GPU에서 만든 결과를 본선에서 다시 CPU로 끌어내리고 있다.

### Output Side

현재 writer는:

- `cv::Mat`만 받는다
- 최신 frame을 보관할 때 복사한다
- worker thread에서 다시 복사한다
- write 직전에 다시 복사한다
- 최종적으로 외부 `ffmpeg` stdin에 `rawvideo`로 쓴다

관련 코드:

- `native_runtime/src/output/ffmpeg_output_writer.h`
- `native_runtime/src/output/ffmpeg_output_writer.cpp`

즉 본선 output은 아직 `GPU direct encode`가 아니라 `CPU rawvideo handoff`다.

### Probe Side

probe는 보조 기능이어야 하지만,
설계가 흐트러지면 transmit와 비슷한 비용을 가질 수 있다.

따라서 probe는 항상 다음 원칙을 따라야 한다.

- 본선 transmit와 완전 분리
- 기본은 lightweight
- 성능 문제 분석용이 아니면 본선과 같은 비용을 쓰지 않음

## Bottom-Line Diagnosis

현재 성능 문제의 핵심은 아래 세 줄로 요약할 수 있다.

1. GPU가 부족해서 못 도는 것이 아니다.
2. GPU 앞뒤에서 CPU로 왕복하는 비용이 크다.
3. 지금 구조는 60fps 서비스용 구조가 아니라, 30fps급 운영 baseline에서 출발한 구조다.

## Target Architecture

본선 서비스 경로는 아래처럼 바뀌어야 한다.

`RTSP ingest -> timestamped frame queue -> GPU decode or zero-copy-friendly decode -> GPU stitch -> GPU scale/colorspace -> in-process NVENC -> transmit`

probe는 아래 둘 중 하나만 허용한다.

1. 본선 비트스트림 mirror receive
2. 더 낮은 해상도/비트레이트의 별도 lightweight debug stream

금지할 구조:

- probe 때문에 본선이 느려지는 구조
- 본선 transmit가 CPU rawvideo pipe에 묶여 있는 구조

## Program-Level Goal

최종 완료 기준은 다음과 같다.

### Service Goal

- `2x60fps` 입력
- steady-state stitched fps `>= 60`
- transmit output fps `>= 60`
- repeated transmit frame ratio `<= 1.05`
- pair skew and input age가 운영 기준 안에 유지

### Debug Goal

- probe를 켜도 본선 transmit fps가 유의미하게 떨어지지 않는다
- viewer/VLC는 본선 문제와 probe 문제를 명확히 구분 가능하다

## Non-Goals

이번 계획의 우선순위 밖인 것:

- calibration 품질 개선
- deep matcher 실제 연결
- UI polish
- 운영 편의 기능 추가

이 항목들은 본선 60fps 구조가 잡힌 뒤 다시 다룬다.

## Execution Strategy

핵심 전략은 `기존 경로 보존 + 새 본선 경로 추가`다.

즉 기존 `ffmpeg rawvideo writer`를 바로 없애지 않는다.

대신:

1. 기존 경로는 fallback/debug 용도로 유지
2. 새 `high-performance transmit path`를 별도 추가
3. production output만 새 경로로 전환
4. probe는 기존 또는 lightweight 경로로 유지

이 방식이 안전한 이유:

- 기존 동작을 바로 깨지 않는다
- 신규 경로 성능을 독립적으로 검증할 수 있다
- 회귀 시 즉시 fallback 가능하다

## Workstreams

### Workstream A. Measurement Freeze

목적:

- 앞으로의 성능 개선이 진짜인지 판단할 기준을 고정한다.

필요한 것:

- 입력 fps
- stitched fps
- transmit written fps
- repeated transmit ratio
- input age / pair skew
- CPU total / runtime CPU / ffmpeg CPU
- GPU util / encoder util / decoder util / memory

완료 조건:

- synthetic input과 live RTSP 모두에서 같은 형식으로 비교 가능
- 개선 전/후 숫자를 같은 스크립트로 뽑을 수 있음

메모:

- 이미 만든 진단 스크립트를 계속 사용하되, `60fps` 기준 판정 항목을 더 분명히 넣는다.

### Workstream B. Probe Isolation

목적:

- probe가 본선 transmit를 방해하지 못하게 한다.

필요한 것:

- probe 기본 preset을 transmit보다 확실히 가볍게 유지
- mirrored transmit는 필요할 때만 활성화
- probe debug overlay, heavy decode path, extra encode가 본선 기본값에 섞이지 않게 정리

완료 조건:

- probe on/off에 따른 transmit fps 차이가 작다
- 본선과 probe 문제가 분리되어 진단 가능하다

### Workstream C. Input Path Upgrade

목적:

- `ffmpeg -> rawvideo -> CPU copy` 경계를 줄인다.

우선순위:

1. 입력 copy/queue 비용 계측 강화
2. 불필요한 CPU 후처리 축소
3. 가능하면 in-process decode 또는 GPU decode 경로 검토

핵심 판단:

- 60fps 목표에서는 외부 `ffmpeg rawvideo pipe` 입력이 계속 본선으로 남아 있으면 한계가 뚜렷하다.

완료 조건:

- 입력 경로가 steady-state `2x60`을 안정적으로 받아들일 수 있음
- pair stage가 입력 대기 때문에 자주 멈추지 않음

### Workstream D. Pair Scheduler Redesign

목적:

- 단순 "latest/closest" 선택이 아니라 60fps 서비스 기준의 pair policy를 만든다.

필요한 것:

- target cadence 기준 pair scheduler
- allowed jitter window 명확화
- reuse 정책을 운영 옵션이 아니라 scheduler 정책으로 재정의
- pair stage stall 이유를 더 정확히 metrics로 노출

완료 조건:

- pair mismatch 때문에 worker가 자주 idle 상태가 되지 않음
- input jitter가 있어도 steady-state cadence 유지

### Workstream E. GPU-Native Transmit Writer

목적:

- 가장 큰 병목인 `GPU -> CPU -> external ffmpeg -> NVENC` 경로를 제거한다.

필수 요구사항:

- 입력: `cv::cuda::GpuMat` 또는 GPU-resident frame
- 처리: GPU scale/colorspace
- 인코드: in-process NVENC
- 출력: UDP/TS 또는 이후 RTSP/SRT 확장 가능

핵심 포인트:

- 현재 `FfmpegOutputWriter`는 `cv::Mat`만 받는다
- 새 writer는 별도 backend로 추가하는 게 맞다

후보 방향:

1. libavcodec/libavformat + hw frames context
2. NVENC 직접 연동 backend

완료 조건:

- production transmit가 CPU rawvideo pipe 없이 동작
- steady-state에서 GPU encoder 활용률이 실제로 상승
- 같은 조건에서 stitched/transmit fps가 유의미하게 개선

### Workstream F. CPU Copy Reduction

목적:

- 새 backend 전에도 줄일 수 있는 CPU 낭비를 줄인다.

예시:

- output writer 내부 다중 copy 축소
- metrics용 full-frame download 축소
- freeze/motion probe sampling 비용 절감

이 작업은 최종 해법은 아니지만, 신규 backend 개발 중에도 체감 개선을 줄 수 있다.

### Workstream G. 60fps Validation Ladder

목적:

- 갑자기 live RTSP 전체를 붙이지 않고,
  단계별로 60fps 달성 여부를 확인한다.

순서:

1. synthetic `2x60` input
2. synthetic `2x60 -> stitch only`
3. synthetic `2x60 -> stitch -> GPU transmit`
4. live RTSP `2x60` input
5. live end-to-end + probe

완료 조건:

- 각 단계에서 병목이 어디인지 명확히 기록 가능
- 마지막 단계에서만 서비스 관점 체감 점검

## Implementation Order

실제 구현은 다음 순서로 진행한다.

### Phase 0. Planning Freeze

- 이 문서를 기준 계획으로 고정
- 진단 스크립트 출력 항목을 60fps 기준으로 보강

### Phase 1. Probe / Debug Cleanup

- probe를 본선과 완전히 분리
- 본선 성능 측정 시 probe 영향 최소화

### Phase 2. Cheap CPU Wins

- output writer 내부 copy 줄이기
- metrics/download/sample 비용 줄이기
- input-side freeze probe 비용 최소화

목표:

- 현재 구조에서도 "쓸데없는 낭비"를 먼저 제거

### Phase 3. Transmit Writer Abstraction

- output writer interface를 backend 분리 가능하게 개편
- `ffmpeg rawvideo writer`와 `future gpu writer`가 공존 가능하게 설계

### Phase 4. GPU Direct Transmit Backend

- 새 production writer 구현
- `GpuMat -> GPU processing -> NVENC -> transmit`

이 단계가 실제 큰 개선의 핵심이다.

### Phase 5. Input / Pair Upgrade

- 60fps 기준 pair scheduler 재설계
- 필요 시 input decode path 개선

### Phase 6. Full 60fps Validation

- synthetic
- live RTSP
- service-mode long run

## Acceptance Criteria Per Phase

### Phase 2 Done

- 기존 path에서 CPU 낭비 감소가 숫자로 확인됨
- stitched fps 또는 output latency가 유의미하게 개선됨

### Phase 4 Done

- production output이 더 이상 CPU rawvideo pipe에 의존하지 않음
- GPU encoder 활용률이 올라감
- transmit fps와 stitched fps가 더 가까워짐

### Phase 5 Done

- pair stage waiting 비율이 충분히 감소
- live RTSP에서 60fps steady-state 가능성 확인

### Final Done

- `2x60 input -> 60fps transmit`가 재현 가능
- probe on/off가 본선에 큰 영향을 주지 않음
- 문서와 운영 스크립트가 새 구조를 반영함

## Immediate Next Step

다음 구현 시작점은 아래로 고정한다.

1. 진단/측정 기준 정리
2. probe 완전 분리 상태 재확인
3. output writer copy reduction
4. transmit writer abstraction 설계 초안 작성

즉 바로 `GPU direct transmit backend`를 무작정 쓰는 것이 아니라,
먼저 현재 코드가 새 backend를 꽂을 수 있게 구조를 정리하고,
동시에 측정 기준을 고정한다.

## Current Active Step

현재 `v1.0` 브랜치에서 가장 먼저 착수하는 작업은 아래다.

1. `Phase 2`의 cheap CPU wins 중 `output writer copy reduction`
2. `Phase 3` transmit writer abstraction skeleton 추가
3. 그다음 `Phase 0` 측정 기준 보강
4. 이후 `future GPU transmit writer`를 꽂을 수 있는 backend 분리 진행

즉 첫 구현은 "가장 큰 개선"이 아니라,
"큰 개선에 들어가기 전에 본선 경로의 불필요한 CPU 낭비를 먼저 걷어내는 작업"부터 시작한다.

현재 반영 상태:

- `output writer copy reduction` 1차 반영 완료
- `OutputWriter interface + factory` skeleton 반영 완료
- `OutputFrame`을 CPU/GPU mixed carrier로 확장 완료
- `gpu-direct` runtime placeholder backend 연결 완료
- output runtime capability 기반의 `conditional CPU download` 시작
- heavy stitched/warped metrics `sampled update` 시작
- 진단 스크립트에 `60fps service_goal` pass/fail summary 반영 완료
- 다음 단계는 `future GPU writer`가 실제로 들어올 자리와 metrics 기준을 더 분명히 만드는 것

## Working Rule

앞으로 이 주제의 작업을 진행할 때는 아래 원칙을 지킨다.

1. 본선 transmit과 probe를 절대 같은 기준으로 다루지 않는다.
2. "GPU 사용률이 낮다"는 사실만으로 GPU 최적화가 끝났다고 보지 않는다.
3. 성능 개선은 항상 synthetic/live 진단 수치로 확인한다.
4. 60fps 목표를 해칠 경우, 편의 기능보다 본선 경로를 우선한다.

## One-Sentence Summary

현재 프로젝트의 다음 최우선 목표는
`30fps급 native runtime 안정화`가 아니라,
`GPU 중심의 60fps service pipeline으로 본선 transmit 경로를 재구성하는 것`이다.
