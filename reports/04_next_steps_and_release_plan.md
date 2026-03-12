# Next Steps And Release Plan

## Immediate Goal

지금 당장의 목표는 `v0.1 operational baseline`을 닫는 것이다.

뜻:

- calibration이 간단하게 실행되고
- runtime이 stitched stream을 내보내고
- operator가 monitor/viewer로 상태를 확인할 수 있고
- 현재 구조와 사용법이 문서로 정리된 상태

## Priority Order

### 1. Calibration UX Finish

필요한 것:

- assisted-first flow 안정화
- inlier/preview review 확인
- auto baseline을 깨지 않는 candidate selection
- 장면별 calibration 실패 이유 정리

### 2. Runtime Operating Criteria

필요한 것:

- realtime 기본 운영 기준 확정
- strict mode의 역할 정리
- `output_fps`, `age`, `motion`, `viewer` 기준으로 운영 판정 정리

### 3. Long-Run Validation

필요한 것:

- 장시간 실행 중 freeze/stall 확인
- output 유지 여부 확인
- restart/reconnect 정책 확인

자동 soak만이 아니라 실제 live run 검증도 중요하다.

### 4. Control Plane Finish

필요한 것:

- `reload_homography`
- output 변경
- runtime control path 정리
- recalibration operator flow 정리

### 5. Documentation Freeze

필요한 것:

- 루트 README 사용법 고정
- reports 문서 구조 고정
- “지금 프로젝트가 어디까지 됐는지”를 누구나 이해 가능하게 정리

## v0.1 Done Criteria

다음 조건이 맞으면 `v0.1`로 묶을 수 있다.

1. calibration -> preview confirm -> runtime launch 흐름이 일관적이다
2. runtime이 stitched stream을 안정적으로 보낸다
3. operator가 monitor와 viewer로 상태를 바로 확인할 수 있다
4. 실행 명령이 과하게 복잡하지 않다
5. 문서가 현재 구조와 맞다

## What Comes After v0.1

`v0.1` 이후는 운영 baseline 이후의 개선 단계다.

- deep matcher 실제 연결
- calibration quality 향상
- 더 강한 recovery/watchdog
- 더 높은 해상도 또는 더 낮은 지연
- codec / pipeline tuning

즉 이후 단계는 “구조를 바꾸는 일”보다 “품질과 안정성을 끌어올리는 일”이다.

## Final Status In One Sentence

현재 프로젝트는 architecture invention 단계가 아니라,
실제로 돌아가는 native stitching runtime을 운영 가능한 baseline으로 마감하는 단계에 있다.
