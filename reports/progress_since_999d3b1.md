# 커밋 `999d3b1` 이후 진행 보고서

일자: 2026-02-26  
기준 커밋: `999d3b1` (`Improve ghosting handling and add pair01/pair02 validation report`)

## 1) 범위

이 보고서는 마지막 푸시 커밋 이후 코드 및 파이프라인 업데이트를 요약합니다.

- 품질 중심 스티칭 개선
- 영상 동기화/블렌딩 안정화
- 영상 실행용 CLI 프리셋 단순화 (`10s`, `30s`, `full`)
- 검증 실행 및 결과

## 2) 작업 로그(시간순)

1. 디버그 매칭 렌더링 안정화(OpenCV `matchesMask` 타입 처리 수정)
2. 비정상 대형 캔버스 방지용 기하 안전장치 추가
3. 호모그래피 불안정 시 affine fallback 추가
4. 고스팅 위험 겹침 구간에서 적응형 seam-cut 추가
5. 영상 동기화 refine 추가(coarse offset + 지역 탐색)
6. 겹침 통계 기반 노출 보정(gain/bias) 추가
7. 고정 수직 seam에서 행 단위 최소비용 seam 경로로 업그레이드
8. CLI 프리셋 명령 추가:
   - `python -m stitching video-10s --pair <name>`
   - `python -m stitching video-30s --pair <name>`
   - `python -m stitching video-full --pair <name>`
9. 프리셋 실행 시 입력/출력 경로 자동 네이밍 추가
10. full 모드 동작 추가(`max_duration_sec <= 0`이면 가능한 전체 구간 처리)

## 3) 주요 기술 변경

### A. 이미지/영상 품질

- 겹침 영역 기반 노출 보정(`exposure_gain`, `exposure_bias` 리포트 기록)
- seam-cut을 행 단위 동적 seam 경로로 변경해 중앙 경계 가시성 완화
- 겹침 위험도 메트릭에 따른 적응형 seam 블렌딩 유지

### B. 영상 동기화

- luma 상관 기반 coarse 동기화
- coarse 주변 후보를 정합 점수로 재평가하는 refine 단계 추가
- 리포트 확장:
  - `coarse_sync_offset_ms`
  - `estimated_sync_offset_ms`
  - `sync_refine_score`

### C. CLI 사용성

- 최소 입력으로 실행 가능한 프리셋 명령 추가:
  - `video-10s`, `video-30s`, `video-full`
- 지원 방식:
  - `--pair video04` (권장)
  - 또는 명시적 `--left/--right`
  - 생략 시 `input/videos`의 최신 유효 pair 자동 선택
- 출력 파일 자동 생성:
  - `output/videos/{pair}_{preset}_stitched.mp4`
  - `output/videos/{pair}_{preset}_report.json`
  - `output/debug/{pair}_{preset}/`

## 4) 검증 실행

### 프리셋 명령 검증

- `python -m stitching video-10s --pair video04` -> succeeded
- `python -m stitching video-30s --pair video01` -> succeeded
- `python -m stitching video-full --pair video01` -> succeeded

### 핵심 결과 스냅샷

1. `video04_10s_report.json`
   - status: succeeded
   - sync: `541.67 ms`
   - blend: `seam_cut`
   - overlap diff: `45.439`
   - exposure: `gain=1.1176`, `bias=-10.38`

2. `video01_30s_report.json`
   - status: succeeded
   - sync coarse -> refined: `-866.67 ms -> -733.33 ms`
   - blend: `seam_cut`
   - overlap diff: `9.848`
   - exposure: `gain=1.0406`, `bias=-7.0963`

3. `video01_full_report.json`
   - status: succeeded
   - 위와 동일한 refined sync/blend 프로파일 확인
   - full 구간 처리 동작 확인

## 5) 데이터셋 범위 업데이트

- 사용자 결정으로 `video2`, `video3`는 지속 검증 대상에서 제외
- 현재 활성 검증 셋은 유지된 영상 pair(`video01`, `video04`) 중심

## 6) 남아 있는 한계

- 큰 시차/근거리 3D 객체 이동에서는 seam 개선 후에도 아티팩트가 남음
- 단일 전역 변환 구조의 품질 한계가 어려운 장면에서 여전히 존재
- 운영 수준 품질 목표에는 로컬 워프(mesh/APAP 계열) + 멀티밴드 블렌딩이 유력

## 7) 권장 다음 작업

1. 현재 seam 경로 기반 위에 멀티밴드 블렌딩 옵션 추가
2. 기존 리포트 메트릭 기반 품질 등급(`stable/fallback/risky`) 추가
3. 리뷰 속도 향상을 위해 `left/right/stitched/overlap diff` 모자이크 디버그 출력 추가

