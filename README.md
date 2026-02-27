# Dual Smartphone Video Stitching PoC

이 프로젝트는 **이미 시간 동기화가 완료된** 두 영상을 입력으로 받아
오프라인 파노라마 영상을 생성합니다.

## 현재 범위

- 입력: `left.mp4`, `right.mp4` (동기화 완료 상태)
- 출력:
  - `stitched.mp4`
  - `report.json`
- 기능:
  - 구간 기반 캘리브레이션(H 추정)
  - H 저장/재사용 선택
  - 성능 모드 선택(quality / balanced / fast)
  - Job API(비디오 전용)

## 폴더 구조

```text
Stitching/
  input/
    videos/
      video10_left.mp4
      video10_right.mp4
  output/
    videos/
    debug/
  storage/
    raw/
    debug/
    out/
    report/
    jobs/
```

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

## 새 선택 기능

### 1) H 저장/재사용

- `--homography-mode off|auto|reuse|refresh`
- `--homography-file .\output\videos\video10_homography.json`

예시:

```powershell
# 1회차: H 새로 계산 후 저장
python -m stitching video-30s --pair video10 `
  --homography-mode refresh `
  --homography-file .\output\videos\video10_h.json

# 2회차: 저장된 H 재사용
python -m stitching video-30s --pair video10 `
  --homography-mode reuse `
  --homography-file .\output\videos\video10_h.json
```

### 2) 성능 모드 선택

- `--perf-mode quality|balanced|fast`
- 필요 시 수동 스케일: `--process-scale 0.5`

예시:

```powershell
python -m stitching video-full --pair video10 --perf-mode fast
```

## API 서버 (비디오 전용)

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

## 에러 코드

- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## report.json 핵심 필드

- `status`
- `error_code`
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
