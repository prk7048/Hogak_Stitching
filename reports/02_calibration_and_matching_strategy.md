# Calibration And Matching Strategy

## Goal

calibration의 목적은 좌/우 카메라를 자연스럽게 겹칠 수 있는 homography를 구하고,
그 결과를 runtime에서 바로 사용할 수 있게 만드는 것이다.

현재 방향은 `assisted-first`다.
즉 완전 자동만 믿지 않고, 사람이 초기 힌트를 줄 수 있게 하되 자동 경로보다 나빠지면 채택하지 않는다.

## Core Principles

1. 사용자는 대응점을 `0..n`개까지 찍을 수 있다.
2. 수동 점은 최종 정답점이 아니라 seed다.
3. auto baseline은 항상 먼저 계산한다.
4. assisted 결과가 baseline보다 나쁘면 버린다.
5. 최종 저장은 가장 품질이 좋은 candidate만 사용한다.

즉 수동 입력은 “강제 정답”이 아니라 “매칭을 더 잘하게 만드는 힌트”다.

## Current Assisted Flow

현재 calibration 흐름은 아래처럼 설계되어 있다.

1. 좌/우 대표 프레임 캡처
2. overlap suggestion guide 표시
3. 사용자가 원하는 만큼 대응점 선택
4. `COMPLETE`
5. baseline auto candidate 계산
6. seed-guided assisted candidate 계산
7. candidate 비교
8. inlier match / stitched preview 표시
9. `CONFIRM`
10. homography 저장
11. runtime launch

즉 사용자는 계산 전에 점을 찍고, 계산 후에는 실제로 채택될 결과를 눈으로 확인한다.

## Why Manual Points Are Seed-Only

수동 점을 최종 homography 계산에 직접 강하게 쓰면, 점 수가 적거나 분포가 나쁠 때 결과가 급격히 망가질 수 있다.

그래서 현재 원칙은 다음과 같다.

- 수동 점은 geometry guide를 만드는 참고 정보다
- 최종 homography는 auto/assisted match quality로 결정한다
- assisted가 auto보다 나쁘면 auto를 그대로 유지한다

이 방식은 “점 찍었더니 오히려 더 나빠지는 문제”를 줄이기 위한 것이다.

## Overlap Guide

manual point 선택을 쉽게 하기 위해 UI에는 overlap suggestion guide가 표시된다.

현재 의도:

- 정답 영역 강제가 아니라 추천 영역
- 사용자가 엉뚱한 곳을 찍지 않게 돕는 가벼운 가이드
- 화면을 가리지 않도록 얇은 outline 형태 유지

즉 overlap guide는 calibration 정확도 자체보다 사용성 개선 장치에 가깝다.

## Candidate Selection Rule

현재 calibration 후보는 크게 두 종류다.

- baseline auto
- assisted refined

채택 규칙:

1. auto는 항상 계산한다
2. seed가 있으면 assisted도 계산한다
3. 품질 score가 더 좋은 candidate만 저장한다

즉 assisted는 baseline을 대체하는 게 아니라 개선 후보로만 들어간다.

## Deferred Design: Pooled Matching

현재 구현은 `auto`, `assisted`, `deep`를 각각 후보로 만들고 그중 가장 좋은 하나를 고르는 방식이다.

하지만 더 강한 방향은 아래 구조다.

1. `auto`가 찾은 match 수집
2. `assisted`가 찾은 match 수집
3. `deep`가 찾은 match 수집
4. 세 match set을 하나로 합침
5. 중복 제거
6. source별 confidence와 reprojection 기준으로 재정렬
7. pooled matches로 최종 homography 계산
8. `auto only`, `deep only`, `pooled` 결과를 다시 비교

이 방식의 장점:

- `auto`의 안정성 유지
- `assisted`의 방향성 활용
- `deep`의 강한 matching 보강
- 한 후보만 고를 때 버려지던 좋은 점을 같이 사용할 수 있음

이 방식의 단점:

- 구현 복잡도가 높음
- 중복 제거와 outlier 정리가 까다로움
- 잘못 합치면 오히려 결과가 나빠질 수 있음

현재 판단:

- 방향 자체는 좋다
- 하지만 지금 단계에서는 calibration 품질 기준과 deep backend 기본 연결이 먼저다
- 따라서 pooled matching은 다음 단계 설계안으로 문서에만 유지하고, 당장은 구현하지 않는다

## Deep Learning Direction

딥러닝은 runtime hot path에 넣을 대상이 아니다.
현재 맞는 방향은 calibration / recalibration 단계의 match quality 향상 도구다.

추천 방향:

- calibration 단계에서만 deep matcher 사용
- user seed point를 anchor로 사용
- 그 주변 match를 더 정교하게 보강
- 최종 homography quality가 baseline보다 좋을 때만 채택
- backend 선택은 `LightGlue/SuperPoint` 우선, `LoFTR` 보조 경로로 두고, 없으면 classic fallback 유지

현재 코드는 optional deep backend 연결까지는 되어 있다.
다만 `torch`, `lightglue`, `kornia` 같은 런타임과 모델이 설치된 환경에서만 실제 deep candidate가 계산된다.

## Practical Conclusion

현재 calibration의 가장 중요한 원칙은 이것이다.

> auto baseline을 절대 깨지 않고, assisted와 deep matching은 더 좋아질 때만 개입한다.

이 문서를 기준으로 앞으로 calibration UX와 deep matcher integration을 마무리하면 된다.
