# Pair01/Pair02 스티칭 검증 보고서

일자: 2026-02-24  
프로젝트: Dual Smartphone Stitching PoC (Image MVP)

## 1) 범위

- `pair01`, `pair02` 이미지 스티칭 결과 비교
- 안정 케이스를 훼손하지 않으면서 불안정 변환 케이스의 고스팅 완화 검증

## 2) 입력

- `input/images/pair01_left.jpg`
- `input/images/pair01_right.jpg`
- `input/images/pair02_left.jpg`
- `input/images/pair02_right.jpg`

## 3) 방법 업데이트

고스팅 완화를 위해 적응형 블렌딩 정책을 추가했습니다.

- 안정 변환: `feather` 블렌딩(기존 동작 유지)
- 불안정 변환(affine fallback 또는 overlap 차이 큼): `seam_cut` 블렌딩

판정 신호:

- `overlap_diff_mean >= 18.0` 또는 `homography_unstable_fallback_affine` 경고

## 4) 결과

| Pair | Status | Matches | Inliers | Blend Mode | overlap_diff_mean | Output Resolution | Total Time (s) | Warnings |
|---|---|---:|---:|---|---:|---|---:|---|
| pair01 | succeeded | 115 | 24 | seam_cut | 31.884 | 7583x3847 | 4.6561 | homography_unstable_fallback_affine |
| pair02 | succeeded | 1185 | 1169 | feather | 2.202 | 7467x2834 | 3.6840 | (none) |

## 5) 해석

- `pair01`:
  - 변환 안정성이 낮아 fallback affine이 동작했습니다.
  - 겹침 평균 블렌딩에서 생기는 이중 경계(고스팅)를 줄이기 위해 seam-cut이 선택되었습니다.
- `pair02`:
  - 정합 품질이 높고 일관적입니다.
  - 기존 feather 블렌딩을 유지해 안정적인 출력 동작을 보였습니다.

## 6) 산출물

- `output/images/pair01_stitched.png`
- `output/images/pair02_stitched.png`
- `output/images/pair01_report.json`
- `output/images/pair02_report.json`
- `output/debug/pair01/*`
- `output/debug/pair02/*`

## 7) Go/No-Go

- Go:
  - pair 단위 적응형 블렌딩이 동작했고, 안정 케이스(`pair02`) 회귀가 없습니다.
  - 두 실행 모두 필수 메트릭/리포트 필드가 존재합니다.
- No-Go(운영/제품 관점):
  - 데이터셋이 2쌍으로 작아 일반화 주장 불가. 추가 검증 필요.

## 8) 권장 다음 단계

1. 난이도 높은 케이스 5쌍 이상 추가(시차, 이동 객체, 노출 차 포함)
2. 리포트에 간단 품질 게이트 추가: `quality_tier = stable / fallback / risky`
3. 수동 검토 속도를 위해 side-by-side 디버그 모자이크 출력 추가

