# 60fps Service Pipeline Plan

> Note: this report preserves historical implementation notes. Some `scripts/...` references below point to legacy helper files that have since been removed after the Python direct-entry cleanup.

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

## Current Baseline Note

아래 본문에는 이전 실험 기록도 함께 남아 있다.
현재 기준으로 읽을 때는 아래 baseline을 우선 사실로 본다.

- 현재 live 카메라는 `30fps`급 입력이다.
- 따라서 단기 목표는 `strict fresh 30fps` 안정화다.
- 현재 preferred live baseline은 대체로
  `ffmpeg-cuda + nv12 + input_buffer_frames=8 + gpu-direct + stitched-size transmit`
  조합이다.
- viewer를 켜면 probe는 `standalone` local debug stream을 사용하고,
  `--no-viewer`면 probe는 기본적으로 꺼진다.
- viewer backend는 `ffplay`와 `opencv`만 유지한다.
  예전 VLC low-latency preview 경로는 제거했다.
- 현재 남은 가장 강한 병목 후보는 output path보다
  `right-side input cadence/source jitter`다.

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
- `rawvideo`로 CPU 메모리에 내린다. 현재 preferred baseline은 `nv12`이고, `bgr24`는 비교용 fallback이다.
- native runtime이 그 프레임을 다시 버퍼에 복사한다

관련 코드:

- `-pix_fmt nv12 -f rawvideo -` 또는 `-pix_fmt bgr24 -f rawvideo -`: `native_runtime/src/input/ffmpeg_rtsp_reader.cpp`
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

운영 메모:

- viewer는 본선 transmit mirror가 아니라 standalone probe를 기본으로 본다.
- 기본 원칙은 `service transmit`과 `local debug probe`를 분리하는 것이다.
- 즉 "서비스 전송 경로"와 "로컬 확인 경로"는 기본값부터 분리되어 있어야 한다.

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

현재 이 다음의 실제 분기점은 아래 둘 중 하나다.

1. `Dependency Track`
   OpenCV 또는 별도 SDK/libav 경로를 정비해서 실제 GPU encode 재료를 확보한다.
2. `Integration Track`
   준비된 encode 재료를 `gpu-direct` backend에 연결한다.

현재 상태에서는 `Dependency Track` 확인이 먼저다.

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
- native build에 `gpu-direct dependency track` 감지 로직 추가
- runtime binary에서 `--print-gpu-direct-status`로 현재 dependency 상태 출력 가능
- `scripts/check_gpu_direct_dependencies.py`로 FFmpeg NVENC capability와 native gpu-direct readiness를 함께 점검 가능
- `.third_party/ffmpeg-dev/current`에 FFmpeg shared dev package 설치 완료
- `gpu_direct_output_writer`에 `libavcodec/libavformat + NVENC` 기반 in-process writer 1차 skeleton 반영 완료
- `scripts/smoke_gpu_direct_output.py`로 `file`/`udp` 두 경로 모두 smoke 검증 가능
- `gpu-direct`가 loopback `UDP mpegts` target에도 실제 encoded output을 쓰는 것 확인 완료
- `gpu-direct` submit 경로는 이제 `GpuMat`를 즉시 CPU download하지 않고 writer thread에서 bridge하도록 조정 완료
- 반복 송출 시 같은 frame에 대해 GPU download와 `sws_scale`를 매번 다시 하지 않도록 cache/reuse 최적화 완료
- `production_output_command_line` metrics를 추가해서 `gpu-direct` 내부 mode를 바로 확인 가능
- 현재 smoke 기준 `gpu-direct`는 실제로 `mode=cuda-hwframes-bgra-direct-fill`까지 올라간다
- 핵심 수정은 `gpu-direct` runtime에서 GPU frame을 CPU보다 우선 소비하도록 바꾸고,
  engine이 `gpu-direct` 경로에 불필요한 prepared CPU frame download를 만들지 않게 한 것이다
- 이 단계로 `GpuMat(BGR) -> GPU BGRA conversion -> CUDA hw frame direct fill -> NVENC` 경로가
  `file` smoke와 `tee-preview` smoke 모두에서 실제로 검증되었다
- 따라서 최신 `gpu-direct`는 더 이상 "CPU bridge fallback 중심"이 아니라,
  `GPU-native direct-fill path + CPU fallback` 단계로 올라왔다
- 남은 큰 비용은 `stitch/input pair stage`와 `GpuMat(BGR) -> GPU BGRA conversion` 자체이며,
  다음 단계는 output path보다는 input/pair scheduler 쪽 개선 비중이 커진다

현재 확인된 구현 제약:

- 로컬 OpenCV에는 `cudacodec` header/lib/symbol은 보인다
- 하지만 build info 기준 `NVCUVENC`, `NVCUVID`는 현재 잡혀 있지 않다
- 즉 "OpenCV cudacodec API는 보이지만 실제 NVENC/NVDEC 기능은 꺼져 있는 빌드"일 가능성이 높다

의미:

- `GpuMat -> OpenCV cudacodec::VideoWriter -> NVENC` 경로는 현재 환경에서 바로 쓸 수 없을 수 있다
- 따라서 `Phase 4`의 실제 본체 구현은 다음 둘 중 하나를 먼저 만족해야 한다

1. OpenCV를 `NVCUVENC/NVCUVID` 활성 상태로 재구성
2. OpenCV 대신 별도 `libavcodec/NVENC` 또는 Video Codec SDK 직접 경로를 붙이기

현재 기준으로는 `libavcodec/NVENC` 경로를 먼저 검토한다.
단, 이 경로는 FFmpeg 실행 파일만으로는 부족하고,
native link가 가능한 `include/` + `lib/` dev 패키지가 따로 필요하다.

현재 상태 메모:

- dev package는 확보했고 CMake도 이를 감지한다.
- native build는 `avcodec/avformat/avutil/swscale` 링크까지 통과한다.
- 최신 `gpu-direct`는 "외부 ffmpeg 프로세스 제거"를 넘어서,
  실제 `GpuMat -> hw frame direct fill -> in-process NVENC` 경로까지 사용 가능하다.
- 즉 지금 backend는 `in-process NVENC skeleton + GPU direct-fill primary path + CPU fallback` 단계다.
- `file` target smoke는 통과했다.
- `udp://127.0.0.1:<port>` target smoke도 통과했고, loopback capture + `ffprobe`까지 확인했다.
- latest smoke에서 `production_output_command_line`에 `mode=cuda-hwframes-bgra-direct-fill`이 노출되어,
  현재 `gpu-direct`가 `AV_PIX_FMT_CUDA + hw_frames_ctx` 위에서 direct-fill path를 실제로 사용 중임을 확인했다.
- 최신 smoke 기준 `gpu-direct`는 `tee` muxer 기반 `UDP service leg + local TCP preview leg`까지 동작한다.
- `scripts/smoke_gpu_direct_output.py --mode tee-preview` 검증에서 UDP capture와 TCP preview capture 모두 `ffprobe` 통과를 확인했다.
- 따라서 `gpu-direct`는 이제 "single production target"만이 아니라, 기본 운영형 preview mirror까지 포함한 transmit 경로를 맡을 수 있다.
- FFmpeg NVENC encoder capability를 확인한 결과 `bgr0`, `bgra`, `cuda` 입력도 지원했고,
  그 경로는 현재 smoke에서 실제 동작 확인까지 끝났다.
- `sync_pair_mode=service` 경로를 추가해서 reader buffer 전체에서 freshest valid pair를 찾는 scheduler 골격을 반영했다.
- runtime metrics에는 `wait_both_streams`, `wait_sync_pair`, `wait_next_frame`, `wait_paired_fresh`, `realtime_fallback_pair` 누적 카운터를 추가했다.
- compact monitor와 JSON metrics에서 새 wait counter를 바로 볼 수 있게 했고, `scripts/diagnose_dual_udp_streams.py`에도 delta 요약을 반영했다.
- input reader의 fps timestamp queue는 `vector erase(begin())`에서 `deque pop_front()`로 바꿔 per-frame O(n) 비용을 제거했다.
- runtime metrics에는 `sync_pair_mode`, `left/right_launch_failures`, `left/right_read_failures`, `left/right_reader_restarts`도 추가했다.
- compact monitor에서는 이제 `pair_mode=...`, `waits=(...)`, `read_fail=(...)`, `reader_restart=(...)`를 바로 볼 수 있다.
- `scripts/compare_pair_modes.py`를 추가해서 `latest`와 `service`를 동일 조건의 direct runtime JSON monitor로 연속 비교할 수 있게 했다.
- 이 비교 스크립트는 `probe-source=disabled`와 로컬 file transmit를 사용해서, viewer/probe 영향을 빼고 pair/input 지표를 직접 비교한다.
- 비교 결과는 `output/debug/compare_pair_latest_runtime.log`, `output/debug/compare_pair_service_runtime.log`, `output/debug/compare_pair_modes_summary.json`에 남는다.
- Windows `runtime_launcher`가 더 이상 native runtime을 `cmd.exe /c`로 감싸지 않게 바꿨다.
- 이 수정은 RTSP URL의 `&subtype=0`가 `cmd`에서 분리되어 engine start가 실패하던 문제를 막기 위한 것이다.
- 최신 `scripts/compare_pair_modes.py` 검증에서는 `latest/service` 모두 native runtime launch와 metrics 수집이 실제로 동작했고,
  비교 결과가 `output/debug/compare_pair_modes_summary.json`에 기록되었다.
- `scripts/smoke_gpu_direct_output.py`는 이제 Windows에서 native runtime을 강제 terminate하지 않고
  JSON `shutdown` 명령으로 정상 종료시킨다.
- 이 수정으로 `tee-preview` smoke의 native runtime 종료 코드는 `1`이 아니라 `0`으로 정리되었고,
  smoke 결과 해석에서 "runtime 오류"와 "capture side 종료 흔적"을 분리할 수 있게 되었다.
- `FfmpegRtspReader::copy_buffered_frame()`는 buffered frame을 매번 deep copy하지 않고,
  OpenCV ref-counted `cv::Mat` header 공유로 pair selection copy 비용을 줄이게 바꿨다.
- `FfmpegRtspReader`는 steady-state에서 stale frame slot의 `cv::Mat` storage를 재사용해서,
  input queue가 가득 찬 상태에서 per-frame heap allocation churn을 줄이게 바꿨다.
- `service` scheduler에는 이제 `target cadence` 개념을 넣어서, buffered pair 중 "가장 최신"만이 아니라
  "직전 service pair 이후 다음 박자에 더 가까운 pair"를 고르게 바꿨다.
- cadence target은 output fps를 우선 기준으로 잡고, target이 최신 buffered pair보다 과하게 뒤처지지 않도록
  one-period window로 clamp해서 stale pair를 쫓는 문제를 줄이게 했다.
- `service` mode는 이제 `buffered_frame_infos()`가 들고 온 `cv::Mat` header를 바로 사용해서,
  reader를 다시 역스캔하는 `copy_frame_by_seq()` 왕복 두 번을 없앴다.
- 기존 `stitch_fps`는 pair timestamp 기반이라 짧은 compare에서 `100fps+` 같은 비현실적인 수치가 튈 수 있었다.
  그래서 wall-clock 기준 `stitch_actual_fps`를 새로 추가했고, `compare_pair_modes.py`도 이 값을 우선 사용하게 바꿨다.
- 최신 working 8초 compare 샘플에서는 `service`가 `latest` 대비
  `stitch_actual_fps_last 47.54 -> 47.83`, `stitch_actual_fps_avg_nonzero 45.687 -> 46.735`,
  `pair_skew_ms_last 35.39 -> 25.81`, `wait_sync_pair_delta 28 -> 0`로 측정됐다.
- 같은 샘플에서 `wait_next_frame_delta 123 -> 130`, `realtime_fallback_pair_delta 137 -> 147`는 약간 늘었다.
  즉 현재 `service`는 sync mismatch를 줄이고 actual stitched cadence를 소폭 올리지만,
  freshness/reuse 쪽은 아직 추가 최적화 여지가 남아 있다.
- 이후 `service` 후보 scoring에 `reuse_streak`/`reuse_age_ns`를 반영해서
  재사용 한계에 걸린 pair를 미리 버리고, 같은 `advance_score` 안에서는 더 신선한 재사용 pair를 우선 고르게 바꿨다.
- 최신 단일 compare 샘플(`output/debug/compare_pair_modes_summary.json`)에서는
  `service`가 `latest` 대비 `wait_sync_pair 42 -> 0`, `wait_next_frame 150 -> 86`, `pair_skew_ms_last 19.39 -> 13.54`로 좋아졌지만,
  `realtime_fallback_pair 125 -> 165`는 늘어서 운영값 튜닝이 필요하다는 결론을 얻었다.
- 그래서 해당 구간에서는
  `input_buffer_frames`, `pair_reuse_max_age_ms`, `pair_reuse_max_consecutive`, `sync_match_max_delta_ms`
  조합을 실제 카메라 기준으로 반복 비교해 baseline을 정리했다.
- 현재 튜닝 결과(`output/debug/service_pair_tuning_summary.json`)에서는
  `wider_buffer = input_buffer_frames=6, pair_reuse_max_age_ms=140, pair_reuse_max_consecutive=4, sync_match_max_delta_ms=60`
  조합이 가장 좋은 점수를 기록했다.
- 이 조합에서는 `service`가 `latest` 대비
  `stitch_actual_fps_avg_nonzero 38.025 -> 40.757`, `pair_skew_ms_last 53.21 -> 16.22`,
  `wait_next_frame 94 -> 67`, `realtime_fallback_pair 147 -> 143`, `wait_sync_pair 23 -> 0`로 개선됐다.
- 따라서 현재 사용자 경로 기본값도 이 결과에 맞춰
  `input_buffer_frames=6`, `pair_reuse_max_age_ms=140`, `pair_reuse_max_consecutive=4`
  쪽으로 정렬했다.
- 그 다음 `service`에 "fresh full-pair slack"을 추가해서,
  둘 다 새 프레임인 경우에는 sync window를 아주 조금 넘는 pair도 허용하도록 바꿨다.
  목적은 partial-reuse fallback 대신 full-fresh pair를 더 자주 쓰게 만드는 것이다.
- 최신 20초 compare(`output/debug/compare_pair_modes_summary.json`)에서는
  `service`가 `latest` 대비 `stitch_actual_fps_avg_nonzero 36.758 -> 39.603`,
  `realtime_fallback_pair 424 -> 387`, `wait_sync_pair 25 -> 0`로 좋아졌다.
- 같은 long-run 샘플에서 `wait_next_frame 142 -> 148`, `pair_skew_ms_avg_nonzero 19.942 -> 21.028`는 약간 나빠졌다.
  즉 현재 service tuning은 "sync mismatch와 fallback 압력 감소" 쪽 이득이 더 크지만,
  freshness/latency trade-off는 아직 남아 있다.
- input reader에 `avioflags direct / analyzeduration 0 / probesize 32 / discardcorrupt`를 묶은 더 공격적인 ffmpeg input flag 조합도 시험했다.
  하지만 실제 compare에서 `left/right_read_failures`가 증가하고 runtime이 `waiting for both streams`에 머물러서, 이 조합은 롤백했다.
- 따라서 현재 기준으로는 RTSP input baseline은 여전히 `-fflags nobuffer -flags low_delay`를 유지하는 것이 맞다.
- 현재 live 검증에서는 카메라 입력이 `ffmpeg frame read failed` 상태라 service pair quality 자체를 끝까지 검증하진 못했고,
  대신 새 wait counter가 runtime metrics에 실제로 노출되는 것까지 확인했다.
- `scripts/diagnose_dual_udp_streams.py`도 현재 tuned service 기본값
  (`realtime_hq_1080p`, `input_buffer_frames=6`, `pair_reuse_max_age_ms=140`, `pair_reuse_max_consecutive=4`, `sync_match_max_delta_ms=60`)
  을 받도록 확장했다.
- 최신 `service_goal` baseline(`output/debug/diagnose_dual_udp_service_goal.json`)은 아직 실패다.
  관측값은 `active_stitch_fps_avg=34.84`, `transmit_written_fps_avg=26.10`, `waiting_ratio=0.1875`였고,
  `gpu_util_avg=25.5`, `cpu_total_avg=36.5`였다.
- 이 값은 현재 병목이 "출력 writer"보다도 여전히 `input/pair stage` 쪽에 더 가깝다는 뜻이다.
- 다음 우선순위는 output path보다 `Input Path Upgrade`와 `Pair Scheduler Redesign`으로 이동한다.
- 현재 실카메라는 좌우 RTSP 입력이 사실상 `30fps`급이므로,
  현재 단계의 현실적인 "fresh stitched" 검증 상한은 `30fps`다.
  즉 `60fps fresh stitched`는 현재 카메라 입력 조건으로는 목표로 잡지 않는다.
- 그래서 현재 `Phase 5/6`의 live 기준은
  `strict no-reuse + fresh stitched 30fps 안정화`로 본다.
- 최신 strict baseline은
  `output/debug/diagnose_dual_udp_service_goal_strict30_gpu_direct_cuda.json` 기준
  `active_stitch_fps_avg=19.15`, `transmit_written_fps_avg=20.71`, `waiting_ratio=0.1875`,
  `transmit_to_stitched_ratio=1.685`였다.
  즉 `gpu-direct` 출력과 `service` pair scheduler를 써도 아직 fresh `30fps`에는 못 미친다.
- 같은 strict 조건에서 `ffmpeg-cpu` input runtime은
  `output/debug/diagnose_dual_udp_service_goal_strict30_gpu_direct_cpu.json` 기준
  `active_stitch_fps_avg=7.67`, `waiting_ratio=0.3125`, `transmit_to_stitched_ratio=2.93`까지 악화됐다.
  따라서 현재 live baseline의 input runtime은 계속 `ffmpeg-cuda`로 고정한다.
- `freeze detection off`는 strict fresh 경로에서 결정적인 개선을 만들지 못했다.
  따라서 남은 핵심은 freeze probe가 아니라
  `RTSP -> ffmpeg rawvideo -> CPU read/queue -> pair selection` 경계다.
- `service` scheduler는 이제 "둘 다 이미 쓴 pair"를 후보에서 바로 버린다.
  즉 repeat-only pair를 고른 뒤 나중에 `waiting next frame`으로 빠지던 흐름을
  pair selection 단계에서 먼저 걸러낸다.
- 이와 함께 `waiting next frame` / `waiting paired fresh frame` / `waiting sync pair`
  카운터를 `select_pair_locked()` 단계에서 바로 올리도록 맞췄다.
  그래서 최신 strict service 진단에서는 이제 "실제로 어디서 막히는지"를
  상태 문자열뿐 아니라 누적 카운터로도 더 정확히 볼 수 있다.
- 최신 짧은 strict baseline(`output/debug/diagnose_dual_udp_service_goal_strict30_gpu_direct_cuda_short_after_wait_counter_fix.json`)에서는
  `active_stitch_fps_avg=24.10`, `transmit_written_fps_avg=28.97`, `waiting_ratio=0.583`,
  `wait_next_frame_delta=102`, `wait_paired_fresh_delta=436`으로 관측됐다.
  의미는 "service가 sync mismatch는 잘 줄이지만, 현재 남은 큰 손실은 fresh pair가 부족해서 생기는 idle"이라는 것이다.
- 현재 결론은 아래와 같다.
  1. `viewer/VLC`는 더 이상 핵심 병목이 아니다.
  2. `ffmpeg output`를 `gpu-direct`로 바꾸는 것만으로는 fresh `30fps`를 못 만든다.
  3. 다음 본작업은 input boundary 자체를 줄이거나 대체하는 것이다.
- `Phase 5-3`의 첫 구현으로 `input_pipe_format` 경로를 추가했다.
  현재 preferred baseline은 `nv12` raw pipe이고, `bgr24`는 비교용 fallback으로 남긴다.
- 현재 구현은 `ffmpeg_rtsp_reader`가 `nv12 rawvideo`를 더 작은 frame으로 읽고 queue에 담게 하지만,
  stitch 직전에는 여전히 CPU 쪽 `NV12 -> BGR` fallback을 사용한다.
  즉 "pipe/queue 축소"는 시작했지만, "GPU-side 색변환"까지는 아직 아니다.
- 최신 strict compare에서는 `output off` 조건에서 `service` 기준
  `bgr24 avg_nonzero=28.318` 대비 `nv12 avg_nonzero=29.684`로 소폭 개선이 보였다.
- `Phase 5-3`의 다음 하위 단계로 실제 `GPU-side nv12->BGR conversion`을 시도했지만,
  현재 OpenCV CUDA build는 `cv::cuda::cvtColor(..., COLOR_YUV2BGR_NV12)`와
  `cvtColorTwoPlane` 경로를 지원하지 않았다.
  런타임 로그에서는 `Unknown/unsupported color conversion code`가 확인됐다.
- 그래서 현재 코드는 `gpu_nv12_input_supported_` capability gate를 두고,
  지원되지 않는 환경에서는 `nv12` queue 경로는 유지하되 stitch 입력은 즉시 CPU decode fallback으로 돌아가게 정리했다.
  이 상태에서 GPU warp/blend는 계속 유지된다.
- 이후 `win_process_pipe`의 raw pipe buffer를 먼저 `1 MiB`, 그 다음 `8 MiB`까지 키우며 strict30 live baseline을 다시 측정했다.
- 최신 live strict30 재검증 기준:
  - `nv12 + 1 MiB pipe buffer`: `active_stitch_fps_avg=30.974`, `transmit_written_fps_avg=27.114`, `waiting_ratio=0.350`, `transmit_to_stitched_ratio=1.114`
  - `nv12 + 8 MiB pipe buffer`: `active_stitch_fps_avg=25.259`, `transmit_written_fps_avg=26.801`, `waiting_ratio=0.300`, `transmit_to_stitched_ratio=1.148`
  - `bgr24 + 1 MiB pipe buffer`: `active_stitch_fps_avg=28.466`, `transmit_written_fps_avg=28.408`, `waiting_ratio=0.450`, `transmit_to_stitched_ratio=1.187`
- 해석:
  - `nv12`는 여전히 `bgr24`보다 나은 preferred baseline이다.
  - pipe buffer를 키우면 `waiting_ratio`와 repeat pressure는 줄어드는 방향이 보였지만,
    `active_stitch_fps_avg`는 live variance가 아직 크다.
  - 즉 raw pipe/read buffer 최적화는 의미가 있었지만, strict30 목표를 끝내려면
    여전히 input cadence / fresh pair 공급을 같이 줄여야 한다.
- 이 측정 이후 현재 live strict30의 preferred baseline은
  `ffmpeg-cuda + nv12 + larger pipe buffer + gpu-direct output`으로 유지한다.
- Phase 5 후속 계측으로 `wait_paired_fresh`를 좌/우/양쪽 breakdown으로 나누고,
  reader cadence/read cost metrics를 추가했다.
- 최신 long strict30 진단 기준:
  - `wait_paired_fresh_left_delta=278`
  - `wait_paired_fresh_right_delta=463`
  - `wait_paired_fresh_both_delta=0`
  - `left_avg_frame_interval_ms.avg=29.20`
  - `right_avg_frame_interval_ms.avg=29.69`
  - `left_avg_read_ms.avg=37.00`
  - `right_avg_read_ms.avg=37.48`
- 해석:
  - 현재 fresh pair 부족은 양쪽 동시 부족보다 `right side fresh 공급 부족` 쪽이 더 크다.
  - pipe/read buffer 최적화 이후 steady-state read cost는 양쪽 모두 대략 `33~38ms` 수준까지 내려오지만,
    초기 warmup spike는 여전히 크다.
  - 따라서 다음 Phase 5 본작업은 "reader raw read 비용" 하나만 더 줄이는 것보다,
    `right-heavy fresh wait`를 줄이도록 pair policy와 input cadence를 함께 보는 것이다.
- 따라서 현재 환경에서는 `GPU-side nv12->BGR conversion`을 더 파는 것보다,
  `reader/queue/RTSP input boundary` 자체를 더 줄이는 작업이 Phase 5의 다음 본작업이다.
- 추가 계측으로 `selected pair`와 `latest frame` 사이 lag, queue span, `wait_paired_fresh` 시점 평균 input age를 넣었다.
- 최신 long strict30 진단(`output/debug/diagnose_dual_udp_service_goal_strict30_gpu_direct_nv12_phase5_rightlag_longrun.json`) 기준:
  - `active_stitch_fps_avg=26.12`
  - `waiting_ratio=0.353`
  - `wait_paired_fresh_left_delta=322`
  - `wait_paired_fresh_right_delta=520`
  - `selected_left_lag_ms.avg=2.50`
  - `selected_right_lag_ms.avg=0.37`
  - `left_age_ms.avg=13.07`
  - `right_age_ms.avg=22.47`
  - `left_buffer_span_ms.avg=145.51`
  - `right_buffer_span_ms.avg=147.38`
- 해석:
  - `selected pair` 자체가 right side에서 뒤처지는 문제보다는, right latest frame의 input age가 더 크고 fresh wait도 더 자주 발생하는 쪽이 더 강하다.
  - 즉 지금 병목은 "scheduler가 right를 오래된 frame으로 고른다"보다 "right fresh frame 공급 cadence가 더 자주 비어 있다"에 가깝다.
- 같은 기준으로 `input_buffer_frames=8`도 시험했다.
  - 결과 파일: `output/debug/diagnose_dual_udp_service_goal_strict30_gpu_direct_nv12_phase5_rightlag_longrun_buf8.json`
  - `active_stitch_fps_avg=28.90`
  - `waiting_ratio=0.294`
  - `wait_paired_fresh_left_delta=423`
  - `wait_paired_fresh_right_delta=390`
  - `transmit_to_stitched_ratio=1.132`
- 현재 해석:
  - queue를 `6 -> 8`로 늘리면 right-heavy bias는 줄고, strict30 baseline도 조금 좋아진다.
  - 하지만 아직 `waiting_ratio <= 0.25`와 `transmit_to_stitched_ratio <= 1.10`을 동시에 만족하지 못한다.
  - 따라서 현재 Phase 5 다음 실작업은 `input_buffer_frames=8`을 baseline 후보로 두고,
    right-side fresh cadence와 `wait_paired_fresh`를 더 줄이는 reader/pair 조정을 이어가는 것이다.
- 이후 기본값을 `input_buffer_frames=8`로 올리고, reader cadence를 더 직접 보기 위해
  `left/right_max_frame_interval_ms`, `left/right_late_frame_intervals` 계측을 추가했다.
- 최신 default-buffer-8 long-run (`output/debug/diagnose_dual_udp_service_goal_strict30_gpu_direct_nv12_phase5_defaultbuf8_longgap.json`)에서는
  - `active_stitch_fps_avg=27.77`
  - `waiting_ratio=0.353`
  - `left_max_frame_interval_ms.avg=67.15`
  - `right_max_frame_interval_ms.avg=73.98`
  - `left_late_frame_intervals.avg=112.29`
  - `right_late_frame_intervals.avg=131.85`
  - `wait_paired_fresh_left_delta=375`
  - `wait_paired_fresh_right_delta=612`
  가 관측됐다.
- 해석:
  - `selected_right_lag_ms`는 거의 `0`인데도 `right_max_frame_interval_ms`와 `right_late_frame_intervals`가 더 크다.
  - 즉 현재 right-heavy 문제는 "scheduler가 right에서 늦은 frame을 고르는 것"보다
    "reader cadence 자체가 right에서 더 자주 길게 비는 것"에 가깝다.
- queue policy 재점검으로 `input_buffer_frames=10`도 시험했다.
  - 결과 파일: `output/debug/diagnose_dual_udp_service_goal_strict30_gpu_direct_nv12_phase5_buf10_longgap.json`
  - `active_stitch_fps_avg=28.50`
  - `waiting_ratio=0.212`
  - `transmit_to_stitched_ratio=1.158`
  - `wait_paired_fresh_left_delta=326`
  - `wait_paired_fresh_right_delta=545`
- 현재 해석:
  - queue depth를 더 늘리면 `waiting_ratio`는 목표선 아래로 내려갈 수 있다.
  - 하지만 아직 `active_stitch_fps >= 30`과 `transmit_to_stitched_ratio <= 1.10`은 동시에 만족하지 못한다.
  - 즉 queue depth 확대는 도움되지만, strict fresh 30fps를 끝내려면 cadence 흔들림 자체를 더 줄여야 한다.

## Working Rule

앞으로 이 주제의 작업을 진행할 때는 아래 원칙을 지킨다.

1. 본선 transmit과 probe를 절대 같은 기준으로 다루지 않는다.
2. "GPU 사용률이 낮다"는 사실만으로 GPU 최적화가 끝났다고 보지 않는다.
3. 성능 개선은 항상 synthetic/live 진단 수치로 확인한다.
4. 60fps 목표를 해칠 경우, 편의 기능보다 본선 경로를 우선한다.

## One-Sentence Summary

현재 프로젝트의 다음 최우선 목표는
현재 카메라 조건에서는 `strict fresh 30fps stitched pipeline`을 먼저 안정화하고,
그 다음에만 `60fps output`과 `future 60fps-fresh input` 확장으로 간다.
