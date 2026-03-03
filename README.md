# Dual Smartphone Video Stitching PoC

오프라인으로 좌/우 스마트폰 영상을 받아 파노라마 영상을 생성하는 프로젝트입니다.
현재 기준은 **비디오 스티칭 MVP**이며, 결과물(`.mp4`)과 `report.json`을 함께 생성합니다.

## 1) 설치
```powershell
python -m pip install -r requirements.txt
```

## 2) 빠른 실행
```powershell
python -m stitching video-10s --pair video10
python -m stitching video-30s --pair video10
python -m stitching video-full --pair video10
```

## 3) 수동 실행
```powershell
python -m stitching video `
  --left .\input\videos\video10_left.mp4 `
  --right .\input\videos\video10_right.mp4 `
  --out .\output\videos\video10_manual.mp4 `
  --report .\output\videos\video10_manual_report.json `
  --debug-dir .\output\debug\video10_manual `
  --max-duration-sec 30
```

## 4) 주요 옵션
- H 저장/재사용
  - `--homography-mode off|auto|reuse|refresh`
  - `--homography-file .\output\videos\video10_h.json`
- 성능 모드
  - `--perf-mode quality|balanced|fast`
  - `--process-scale 0.5` (필요 시 수동 지정)
- seam 실험 옵션(기본 비활성)
  - `--adaptive-seam on|off` (기본 `off`)
  - `--seam-update-interval 12`
  - `--seam-temporal-penalty 1.5`
  - `--seam-motion-weight 1.5`

## 5) API 서버(선택)
```powershell
python -m stitching serve --host 127.0.0.1 --port 8080 --storage-dir .\storage
```

작업 생성:
```powershell
curl -X POST http://127.0.0.1:8080/jobs/video-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/video_left.mp4\",\"right_path\":\"C:/path/to/video_right.mp4\",\"options\":{\"max_duration_sec\":30,\"perf_mode\":\"balanced\",\"homography_mode\":\"auto\"}}"
```

작업 조회:
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/report`
- `GET /jobs/{job_id}/artifact`

## 6) 에러 코드
- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## 7) report.json 핵심 필드
- `status`
- `error_code`
- `reason_detail`
- `metrics.matches_count`
- `metrics.inliers_count`
- `metrics.processing_time_sec`
- `metrics.output_resolution`
- `metrics.processed_frames`
- `metrics.blend_mode`
- `metrics.overlap_diff_mean`
