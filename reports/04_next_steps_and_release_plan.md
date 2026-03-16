## Immediate Goal

지금 당장의 목표는 `strict fresh 30fps live baseline`을 운영 기준으로 닫는 것이다.

뜻:

- 현재 `30fps`급 카메라 입력 조건에서
- `gpu-direct` transmit baseline을 유지하고
- input/source 흔들림이 실제 병목인지 분리 확인하고
- 장시간 실행에서도 운영 가능한지 검증하는 상태

## Priority Order

### 1. Source / Cadence Diagnosis

필요한 것:

- 좌/우 카메라를 바꿔서 문제가 `right 자리`를 따라가는지 확인
- 좌/우 RTSP를 단독으로 받아 cadence/gap을 비교
- 가능하면 Wi-Fi/UDP 영향과 source 자체 영향 분리
- 카메라 fps / GOP / bitrate / transport 조건 재확인

목적:

- 지금 남은 문제가 code path인지 source path인지 먼저 가른다.

### 2. Strict Fresh 30 Long-Run Validation

필요한 것:

- `ffmpeg-cuda + nv12 + input_buffer_frames=8 + gpu-direct`
- viewer off / probe disabled baseline
- 30분 이상 long-run
- `active_stitch_fps`, `waiting_ratio`, `transmit_to_stitched_ratio`, read/restart 지표 기록

목적:

- 현재 baseline이 운영 가능한 수준인지 먼저 판정한다.

### 3. Limited Code Follow-Up Only If Needed

필요한 것:

- source 문제가 아니라 code 병목으로 확인된 경우에만
  - reader cadence 계측 추가
  - queue/pair 정책 미세조정
  - input boundary 최적화 추가

원칙:

- 이미 효과가 작았던 queue 미세조정에 오래 머물지 않는다.

### 4. Documentation Freeze

필요한 것:

- 루트 README 실행 방법 고정
- reports 현재 상태 반영
- viewer/backend/preset 설명 정리

목적:

- 누구나 현재 baseline과 다음 단계가 무엇인지 바로 이해할 수 있게 한다.

## Done Criteria For Current Stage

다음 조건이 맞으면 현재 단계를 닫을 수 있다.

1. 메인 실행 경로가 문서와 일치한다
2. `strict fresh 30fps` long-run 결과가 충분히 해석 가능하다
3. source 문제인지 code 문제인지 분리 판단이 가능하다
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

## Final Status In One Sentence

현재 프로젝트의 다음 단계는
"새 기능을 더 얹는 것"보다 "`strict fresh 30fps` 기준으로 source와 code 병목을 분리해서 운영 baseline을 닫는 것"에 가깝다.
