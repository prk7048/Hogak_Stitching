# Reports 안내

이 디렉터리는 현재 프로젝트의 의사결정, 알고리즘 정책, 개발 진행 기록을 정리한 문서 모음이다.

현재 기준으로 **우선 읽어야 할 문서**는 아래 3개다.

## 1. [01_런타임_구조_전환.md](c:\Users\Pixellot\Hogak_Stitching\reports\01_런타임_구조_전환.md)

내용:
- 왜 현재 Python/OpenCV 구조로는 60fps급 목표가 어려운지
- 왜 `Python 제어 + Native 엔진` 구조로 전환하려는지
- OpenCV 입력, FFmpeg direct 입력 실험에서 무엇을 확인했는지
- 왜 `A(standalone runtime)`를 먼저 가고, 이후 `B(GStreamer)` / `C(FFmpeg 중심)`까지 열어두는지
- 왜 최종 결과 확인은 `final output stream viewer` 방식으로 가는지

대상:
- 런타임 구조, 성능 병목, 구현 순서를 이해하려는 경우

## 2. [02_스티칭_알고리즘_정책.md](c:\Users\Pixellot\Hogak_Stitching\reports\02_스티칭_알고리즘_정책.md)

내용:
- 특징점 매칭 / 기하 변환 / 블렌딩 / seam-cut 정책 정리
- 현재 코드에서 쓰는 방식과 대안의 장단점
- 수동 포인트, 초기 1회 보정, 재보정 전략
- 딥러닝 매칭(SuperPoint/LightGlue/LoFTR) 검토 결과
- 현재 카메라 환경에 맞는 추천 정책

대상:
- 스티칭 품질과 알고리즘 방향을 정리하려는 경우

## 3. [03_개발_로그.md](c:\Users\Pixellot\Hogak_Stitching\reports\03_개발_로그.md)

내용:
- 주요 변경 이력 요약
- 무엇을 시도했고, 무엇을 버렸고, 왜 그런 결정을 했는지

대상:
- 지금까지의 진행 상황을 빠르게 훑고 싶은 경우

## 보관 문서

이전에 작성한 세부 설계/영문 문서는 `archive/` 아래에 보관한다.

원칙:
- 루트 `reports/`에는 지금 기준으로 바로 참고해야 하는 문서만 둔다
- 세부 이력이나 중간 산출물은 `archive/`로 내린다

## 로컬 전용 문서

아래 문서는 로컬 참고용이며 Git 기준 핵심 문서가 아니다.

- `session_transcript_2026-03-06_reconstructed.md`

이 문서는 세션 재구성본이므로 설계 기준 문서로 취급하지 않는다.
