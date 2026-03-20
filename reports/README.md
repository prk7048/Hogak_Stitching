# Reports

이 디렉터리는 사용법보다 판단 배경과 현재 상태를 설명하는 문서를 모아둔 곳이다.

루트 [README.md](/c:/Users/Pixellot/Hogak_Stitching/README.md)가 실행 중심 요약이라면,
`reports/`는 왜 이렇게 구성됐는지와 지금 어디까지 왔는지를 정리한 보조 문서 세트다.

권장 읽기 순서:

1. [01_project_overview_and_architecture.md](/c:/Users/Pixellot/Hogak_Stitching/reports/01_project_overview_and_architecture.md)
2. [02_calibration_and_matching_strategy.md](/c:/Users/Pixellot/Hogak_Stitching/reports/02_calibration_and_matching_strategy.md)
3. [03_native_runtime_current_status.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_native_runtime_current_status.md)
4. [04_next_steps_and_release_plan.md](/c:/Users/Pixellot/Hogak_Stitching/reports/04_next_steps_and_release_plan.md)
5. [05_60fps_service_pipeline_plan.md](/c:/Users/Pixellot/Hogak_Stitching/reports/05_60fps_service_pipeline_plan.md)
6. [06_deployment_and_support_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/06_deployment_and_support_guide.md)
7. [07_new_hire_handoff_study_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/07_new_hire_handoff_study_guide.md)
8. [08_runtime_architecture_diagrams.md](/c:/Users/Pixellot/Hogak_Stitching/reports/08_runtime_architecture_diagrams.md)

빠른 역할 요약:

- `01`: 프로젝트 목적과 전체 구조
- `02`: calibration 방식과 matching 전략
- `03`: 현재 운영 baseline과 남은 리스크
- `04`: 다음 우선순위와 검증 계획
- `05`: 장기 60fps 파이프라인 계획
- `06`: 배포 구조와 지원 환경 기준
- `07`: 완전 초급 기준의 상세 인수인계/학습 문서와 실습 과제
- `08`: Mermaid.js 기반 구조도와 runtime 흐름도

현재 live 카메라가 `30fps`급이라, `03`과 `04`는 단기 목표를 `strict fresh 30fps baseline` 기준으로 설명한다.
`05`는 그보다 긴 호흡의 `future 60fps` 구조 계획을 다룬다.
`07`은 운영 보고서라기보다 후임 온보딩과 자가 학습을 위한 참고서에 가깝다.
`08`은 코드 리뷰나 인수인계 때 전체 구조를 한 번에 설명할 때 쓰기 좋다.
