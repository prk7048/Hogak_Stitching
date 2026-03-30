# Reports

이 디렉터리는 실행 방법보다는 아키텍처, 현재 상태, 배포 기준, 온보딩 자료를 모아둔 곳이다.

루트 [README.md](/c:/Users/Pixellot/Hogak_Stitching/README.md)는 실행 중심 입구 문서이고, `reports/`는 이유와 배경을 설명하는 참고 문서 모음이다.

## Start Here

프로젝트를 빠르게 파악하려면 이 순서가 가장 좋다.

1. [01_project_overview_and_architecture.md](/c:/Users/Pixellot/Hogak_Stitching/reports/01_project_overview_and_architecture.md)
2. [03_current_status_and_roadmap.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_current_status_and_roadmap.md)
3. [08_runtime_architecture_diagrams.md](/c:/Users/Pixellot/Hogak_Stitching/reports/08_runtime_architecture_diagrams.md)
4. [09_baseline_acceptance_and_source_timing.md](/c:/Users/Pixellot/Hogak_Stitching/reports/09_baseline_acceptance_and_source_timing.md)

## Document Map

- [01_project_overview_and_architecture.md](/c:/Users/Pixellot/Hogak_Stitching/reports/01_project_overview_and_architecture.md)
  - 프로젝트 목적, 구성 요소, 운영 기준
- [02_calibration_and_matching_strategy.md](/c:/Users/Pixellot/Hogak_Stitching/reports/02_calibration_and_matching_strategy.md)
  - calibration 철학과 classic matcher 기준
- [03_current_status_and_roadmap.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_current_status_and_roadmap.md)
  - 현재 baseline, 남은 리스크, 바로 다음 우선순위
- [06_deployment_and_support_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/06_deployment_and_support_guide.md)
  - 배포 구조, 지원 환경, 운영 기준
- [07_new_hire_handoff_study_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/07_new_hire_handoff_study_guide.md)
  - 완전 초급 기준의 인수인계/학습 문서와 실습 과제
- [08_runtime_architecture_diagrams.md](/c:/Users/Pixellot/Hogak_Stitching/reports/08_runtime_architecture_diagrams.md)
  - Mermaid.js 기반 구조도와 데이터 흐름도
- [09_baseline_acceptance_and_source_timing.md](/c:/Users/Pixellot/Hogak_Stitching/reports/09_baseline_acceptance_and_source_timing.md)
  - strict fresh 30 baseline, arrival/source timing 모델, `native-validate` 절차, smoke/10분/30분 acceptance 기준

## Historical Notes

- [05_60fps_service_pipeline_plan.md](/c:/Users/Pixellot/Hogak_Stitching/reports/05_60fps_service_pipeline_plan.md)
  - 장기 `future 60fps` 재설계 문서
  - 현재 운영 기준 문서라기보다 기록과 장기 방향 참고서에 가깝다

## When To Open Which File

- 지금 구조가 어떻게 생겼는지 알고 싶다
  - [01_project_overview_and_architecture.md](/c:/Users/Pixellot/Hogak_Stitching/reports/01_project_overview_and_architecture.md)
  - [08_runtime_architecture_diagrams.md](/c:/Users/Pixellot/Hogak_Stitching/reports/08_runtime_architecture_diagrams.md)
- 지금 당장 어디가 남은 일인지 알고 싶다
  - [03_current_status_and_roadmap.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_current_status_and_roadmap.md)
- 이번 단계에서 무엇을 pass로 볼지 알고 싶다
  - [09_baseline_acceptance_and_source_timing.md](/c:/Users/Pixellot/Hogak_Stitching/reports/09_baseline_acceptance_and_source_timing.md)
- calibration 쪽만 보고 싶다
  - [02_calibration_and_matching_strategy.md](/c:/Users/Pixellot/Hogak_Stitching/reports/02_calibration_and_matching_strategy.md)
- 배포와 운영 기준을 보고 싶다
  - [06_deployment_and_support_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/06_deployment_and_support_guide.md)
- 신입 온보딩 자료가 필요하다
  - [07_new_hire_handoff_study_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/07_new_hire_handoff_study_guide.md)

## Redesign Pack

- [10_end_to_end_redesign_spec.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/10_end_to_end_redesign_spec.md)
  - approved redesign baseline, subsystem ownership, and migration order
- [11_runtime_contract_and_supervisor_spec.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/11_runtime_contract_and_supervisor_spec.md)
  - schema v2, runtime supervisor, and control-plane cleanup
- [12_geometry_cylindrical_spec.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/12_geometry_cylindrical_spec.md)
  - cylindrical baseline, geometry artifact, and blend policy
- [13_validation_rollout_and_acceptance_spec.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/13_validation_rollout_and_acceptance_spec.md)
  - read-only validation boundary, rollout, and acceptance metrics

### ADRs

- [ADR-001-runtime-schema-v2.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/adr/ADR-001-runtime-schema-v2.md)
- [ADR-002-prepare-run-validate-boundary.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/adr/ADR-002-prepare-run-validate-boundary.md)
- [ADR-003-cylindrical-baseline.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/adr/ADR-003-cylindrical-baseline.md)
- [ADR-004-native-transmit-and-reproducible-build.md](/C:/Users/prk70/OneDrive/바탕%20화면/새%20폴더%20(2)/reports/adr/ADR-004-native-transmit-and-reproducible-build.md)
