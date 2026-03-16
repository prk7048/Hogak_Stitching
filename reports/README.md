# Reports

이 디렉터리는 현재 프로젝트 상태를 빠르게 파악하기 위한 문서만 남겨둔 정리본이다.
중간 설계 메모, 세션 재구성본, 스켈레톤 단계 기록은 제거하고 현재 기준으로 필요한 문서만 유지한다.

권장 읽기 순서:

1. [01_project_overview_and_architecture.md](/c:/Users/Pixellot/Hogak_Stitching/reports/01_project_overview_and_architecture.md)
2. [02_calibration_and_matching_strategy.md](/c:/Users/Pixellot/Hogak_Stitching/reports/02_calibration_and_matching_strategy.md)
3. [03_native_runtime_current_status.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_native_runtime_current_status.md)
4. [04_next_steps_and_release_plan.md](/c:/Users/Pixellot/Hogak_Stitching/reports/04_next_steps_and_release_plan.md)
5. [05_60fps_service_pipeline_plan.md](/c:/Users/Pixellot/Hogak_Stitching/reports/05_60fps_service_pipeline_plan.md)
6. [06_deployment_and_support_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/06_deployment_and_support_guide.md)

문서 역할:

- `01`: 프로젝트 목표, 전체 구조, 실행 흐름
- `02`: calibration 전략, assisted matching 방향, deep matching 확장 방향
- `03`: 현재 코드 기준 운영 baseline과 남은 리스크
- `04`: 현재 우선순위, source 진단, strict fresh 30 검증 계획
- `05`: 장기 목표인 `2x60 input -> 60fps transmit`를 위한 본선 파이프라인 재설계 계획
- `06`: 환경의존성 제거 1~5차 결과, 배포 bundle 구조, 지원 환경 기준

루트 [`README.md`](/c:/Users/Pixellot/Hogak_Stitching/README.md)는 사용법 중심이고,
`reports/`는 판단 배경과 현재 상태를 정리한 보조 문서 세트다.

현재 live 카메라가 `30fps`급인 점 때문에,
`03`과 `04`는 단기 목표를 `strict fresh 30fps baseline` 기준으로 설명하고,
`05`는 그보다 긴 호흡의 `future 60fps` 구조 계획을 다룬다.
