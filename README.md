# Dual Smartphone Video Stitching PoC

이 프로젝트는 **시간 동기화가 이미 완료된** 좌/우 스마트폰 영상을 입력으로 받아,
오프라인 파노라마 영상을 생성하는 MVP입니다.

## 현재 MVP 범위
- 입력: `left.mp4`, `right.mp4`
- 출력:
  - `stitched.mp4`
  - `report.json`
- 핵심 기능:
  - 구간 기반 캘리브레이션(H 추정)
  - H 저장/재사용(`off/auto/reuse/refresh`)
  - 성능 모드(`quality/balanced/fast`)
  - Job API(`serve`) + queue/worker + storage

## 설치
```powershell
python -m pip install -r requirements.txt
```

## 실행
### 프리셋 실행
```powershell
python -m stitching video-10s --pair video10
python -m stitching video-30s --pair video10
python -m stitching video-full --pair video10
```

### 수동 실행
```powershell
python -m stitching video `
  --left .\input\videos\video10_left.mp4 `
  --right .\input\videos\video10_right.mp4 `
  --out .\output\videos\video10_manual.mp4 `
  --report .\output\videos\video10_manual_report.json `
  --debug-dir .\output\debug\video10_manual `
  --max-duration-sec 30
```

## 선택 기능
### H 저장/재사용
- `--homography-mode off|auto|reuse|refresh`
- `--homography-file .\output\videos\video10_h.json`

예시:
```powershell
# 새로 계산 후 저장
python -m stitching video-30s --pair video10 `
  --homography-mode refresh `
  --homography-file .\output\videos\video10_h.json

# 저장된 H 재사용
python -m stitching video-30s --pair video10 `
  --homography-mode reuse `
  --homography-file .\output\videos\video10_h.json
```

### 성능 모드
- `--perf-mode quality|balanced|fast`
- 필요 시 수동 스케일: `--process-scale 0.5`

## API 서버
### 서버 실행
```powershell
python -m stitching serve --host 127.0.0.1 --port 8080 --storage-dir .\storage
```

### 작업 생성
```powershell
curl -X POST http://127.0.0.1:8080/jobs/video-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/video10_left.mp4\",\"right_path\":\"C:/path/to/video10_right.mp4\",\"options\":{\"max_duration_sec\":30,\"perf_mode\":\"balanced\",\"homography_mode\":\"auto\"}}"
```

### 작업 조회
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/report`
- `GET /jobs/{job_id}/artifact`

## 에러 코드
- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## report.json 주요 필드
- `status`
- `error_code`
- `reason_detail`
- `metrics.matches_count`
- `metrics.inliers_count`
- `metrics.processing_time_sec`
- `metrics.output_resolution`
- `metrics.processed_frames`
- `metrics.calib_used_time_sec`
- `metrics.overlap_diff_mean`
- `metrics.homography_mode_requested`
- `metrics.homography_source`
- `metrics.perf_mode`
- `metrics.processing_scale`

## 2026-03-03 리팩토링 반영
- `stitch_core.py`를 기능별 모듈로 분리:
  - `stitching/core/config.py`
  - `stitching/core/features.py`
  - `stitching/core/geometry.py`
  - `stitching/core/blend.py`
  - `stitching/core/exposure.py`
- `stitching/stitch_core.py`는 하위 호환용 facade로 유지
- 성능 프로필 로직을 `stitching/perf_profiles.py`로 통합
  - CLI, JobService가 공통 함수 사용
